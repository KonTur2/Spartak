from datetime import datetime

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Sum, Q, Case, When, IntegerField
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from accounts.decorators import role_required
from .models import Notification, Ship, CrewMember, Voyage, Maintenance, Contractor, MaintenanceLog, RepairWork
from .services import (
    annotate_maintenance_health_priority,
    annotate_voyage_health_priority,
    ensure_default_ship_crew_requirements,
    ensure_default_repair_works,
    ensure_planned_maintenance_for_ship,
    enrich_ship_for_operations,
    filter_maintenances_by_health,
    filter_voyages_by_health,
    get_planned_maintenance_status,
    get_system_problem_states,
    group_problem_issues_by_code,
    ship_readiness_service,
    validate_crew_for_voyage_period,
)


OPEN_VOYAGE_DELTA = timezone.timedelta(days=3650)
OPEN_MAINTENANCE_DELTA = timezone.timedelta(days=3650)
DATE_INPUT_PLACEHOLDER = 'дд.мм.гггг'
DATETIME_INPUT_PLACEHOLDER = 'дд.мм.гггг чч:мм'


def _get_choice_param(request, name, allowed_values, default):
    value = request.GET.get(name, default)
    if value not in allowed_values:
        return default
    return value


def _parse_local_date(raw_value):
    if raw_value in (None, ''):
        return None
    value = str(raw_value).strip()
    for date_format in ('%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return parse_date(value)


def _parse_local_datetime(raw_value):
    if raw_value in (None, ''):
        return None
    value = str(raw_value).strip()
    for datetime_format in (
        '%d.%m.%Y %H:%M',
        '%d.%m.%y %H:%M',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d %H:%M:%S',
    ):
        try:
            return datetime.strptime(value, datetime_format)
        except ValueError:
            continue
    return parse_datetime(value)


def _format_date_input(value):
    if not value:
        return ''
    return value.strftime('%d.%m.%Y')


def _format_datetime_input(value):
    if not value:
        return ''
    return value.strftime('%d.%m.%Y %H:%M')


def _parse_engine_hours(raw_value):
    if raw_value in (None, ''):
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValidationError('Наработка двигателя должна быть целым неотрицательным числом.')
    if value < 0:
        raise ValidationError('Наработка двигателя не может быть отрицательной.')
    return value


def _get_current_ship_engine_hours(ship):
    if ship is None:
        return None
    latest_log = ship.maintenance_logs.order_by('-recorded_at', '-id').first()
    if latest_log:
        return latest_log.engine_hours
    return ship.engine_hours


def _validation_messages(error):
    if hasattr(error, 'error_dict'):
        return [str(item) for errors in error.error_dict.values() for item in errors]
    if hasattr(error, 'messages'):
        return [str(message) for message in error.messages]
    return [str(error)]


def _get_user_role(user):
    role_obj = getattr(user, 'role', None)
    return getattr(role_obj, 'role', None)


def _resolve_captain_panel_ship(request):
    user_role = _get_user_role(request.user)
    if user_role == 'captain':
        crew_profile = getattr(request.user, 'crew_profile', None)
        if not crew_profile or crew_profile.rank != 'captain' or not crew_profile.assigned_ship_id:
            return None, []
        ship = (
            Ship.objects.filter(id=crew_profile.assigned_ship_id)
            .prefetch_related('maintenance_logs__author')
            .first()
        )
        return ship, []

    available_ships = list(Ship.objects.all().order_by('name'))
    ship_id = request.GET.get('ship_id') or request.POST.get('ship_id')
    selected_ship = None
    if ship_id:
        selected_ship = get_object_or_404(
            Ship.objects.prefetch_related('maintenance_logs__author'),
            id=ship_id,
        )
    elif available_ships:
        selected_ship = Ship.objects.prefetch_related('maintenance_logs__author').get(id=available_ships[0].id)
    return selected_ship, available_ships


def _get_captain_assigned_ship_id(user):
    crew_profile = getattr(user, 'crew_profile', None)
    if not crew_profile or crew_profile.rank != 'captain':
        return None
    return crew_profile.assigned_ship_id


def _role_for_crew_rank(rank):
    return 'captain' if rank == 'captain' else 'crew'


def _sync_user_role_with_crew_rank(user, rank):
    user_role = getattr(user, 'role', None)
    if not user_role:
        return
    target_role = _role_for_crew_rank(rank)
    if user_role.role != target_role:
        user_role.role = target_role
        user_role.save(update_fields=['role'])


def _open_voyage_end(end_date):
    return end_date or (timezone.now() + OPEN_VOYAGE_DELTA)


def _open_maintenance_end(end_date):
    return end_date or (timezone.now().date() + OPEN_MAINTENANCE_DELTA)


def _find_overlapping_crew(crew_queryset, start_date, end_date, exclude_voyage_id=None):
    overlapping_crew = []
    effective_end = _open_voyage_end(end_date)
    for crew_member in crew_queryset:
        overlapping_voyages = Voyage.objects.filter(
            crew=crew_member,
            is_completed=False,
            start_date__lt=effective_end,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gt=start_date)
        )
        if exclude_voyage_id:
            overlapping_voyages = overlapping_voyages.exclude(id=exclude_voyage_id)
        if overlapping_voyages.exists():
            overlapping_crew.append(crew_member)
    return overlapping_crew


def _find_overlapping_voyages_for_maintenance(ship, start_date, end_date, exclude_maintenance_id=None):
    effective_end = _open_maintenance_end(end_date)
    overlapping_voyages = Voyage.objects.filter(
        ship=ship,
        is_completed=False,
        start_date__date__lt=effective_end,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__date__gt=start_date)
    )
    if exclude_maintenance_id:
        overlapping_voyages = overlapping_voyages.exclude(id=exclude_maintenance_id)
    return overlapping_voyages


@login_required
@role_required(['captain', 'engineer', 'dispatcher'])
def captain_panel(request):
    ship, available_ships = _resolve_captain_panel_ship(request)
    user_role = _get_user_role(request.user)

    if request.method == 'POST':
        if ship is None:
            messages.error(request, 'Для вас не найдено закреплённое судно.')
        else:
            note = (request.POST.get('note') or '').strip()
            try:
                if not note:
                    raise ValidationError({'note': 'Введите текст сообщения для диспетчера или инженера.'})

                current_engine_hours = _get_current_ship_engine_hours(ship)
                engine_hours = current_engine_hours if current_engine_hours is not None else 0
                log = MaintenanceLog(
                    ship=ship,
                    engine_hours=engine_hours,
                    note=note,
                    author=request.user,
                )
                log.full_clean()
                log.save()
                messages.success(request, 'Техническая запись сохранена.')
                if user_role == 'captain':
                    return redirect('fleet:captain_panel')
                return redirect(f"{redirect('fleet:captain_panel').url}?ship_id={ship.id}")
            except ValidationError as error:
                for message in _validation_messages(error):
                    messages.error(request, message)

    logs = ship.maintenance_logs.select_related('author').all()[:10] if ship else []
    current_engine_hours = _get_current_ship_engine_hours(ship)

    context = {
        'page_title': 'Журнал технических сообщений',
        'ship': ship,
        'available_ships': available_ships,
        'logs': logs,
        'current_engine_hours': current_engine_hours,
        'is_captain': user_role == 'captain',
        'selected_ship_id': ship.id if ship else None,
        'submitted_note': request.POST.get('note', ''),
    }
    return render(request, 'fleet/captain_panel.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew', 'dispatcher', 'engineer'])
def fleet_dashboard(request):
    """Главная страница модуля управления флотом"""
    ships = list(
        Ship.objects.all()
        .prefetch_related('crew_members__user', 'voyages', 'maintenances__contractor')
        .order_by('name')
    )
    ships = [enrich_ship_for_operations(ship) for ship in ships]
    
    # Определение статуса состава экипажа для каждого судна
    for ship in ships:
        crew_req = ship.readiness.context.get('crew_requirements', {})
        requirements_configured = crew_req.get('requirements_configured', False)
        missing_roles = crew_req.get('missing_roles', [])
        
        if not requirements_configured:
            ship.crew_status = 'not_configured'
            ship.crew_status_display = 'требования не настроены'
            ship.crew_status_class = 'bg-warning'  # желтый (warning)
        elif missing_roles:
            ship.crew_status = 'shortage'
            ship.crew_status_display = 'есть нехватка ролей'
            ship.crew_status_class = 'bg-danger'  # красный
        else:
            ship.crew_status = 'complete'
            ship.crew_status_display = 'состав укомплектован'
            ship.crew_status_class = 'bg-success'  # зеленый
    
    # Фильтрация судов по готовности
    ship_filter = _get_choice_param(request, 'ship_filter', {'all', 'ready', 'blocking', 'warning'}, 'all')
    filtered_ships = ships
    if ship_filter == 'ready':
        filtered_ships = [ship for ship in ships if ship.readiness.is_ready]
    elif ship_filter == 'blocking':
        filtered_ships = [ship for ship in ships if not ship.readiness.is_ready]
    elif ship_filter == 'warning':
        filtered_ships = [ship for ship in ships if ship.readiness.is_ready and ship.readiness.severity_summary.get('warning', 0) > 0]
    # 'all' - без фильтрации
    
    # Фильтрация судов по статусу состава экипажа
    crew_filter = _get_choice_param(request, 'crew_filter', {'all', 'complete', 'shortage'}, 'all')
    if crew_filter == 'complete':
        filtered_ships = [ship for ship in filtered_ships if ship.crew_status == 'complete']
    elif crew_filter == 'shortage':
        filtered_ships = [ship for ship in filtered_ships if ship.crew_status == 'shortage']

    # Сортировка судов
    ship_sort = _get_choice_param(request, 'ship_sort', {'name', 'criticality'}, 'name')
    if ship_sort == 'criticality':
        # Сортировка по критичности: blocking -> warning -> ok
        # При этом blocking - не готовые (is_ready == False)
        # warning - готовые, но есть warning
        # ok - готовые без warning
        def criticality_key(ship):
            if not ship.readiness.is_ready:
                return 0  # blocking
            elif ship.readiness.severity_summary.get('warning', 0) > 0:
                return 1  # warning
            else:
                return 2  # ok
        filtered_ships.sort(key=lambda ship: (criticality_key(ship), ship.name))
    else:  # 'name' (default)
        filtered_ships.sort(key=lambda ship: ship.name)
    
    ships_at_sea = sum(1 for ship in ships if ship.actual_status_code == 'at_sea')
    ships_in_port = sum(1 for ship in ships if ship.actual_status_code == 'in_port')
    ships_under_repair = sum(1 for ship in ships if ship.actual_status_code == 'under_repair')
    ships_ready = sum(1 for ship in ships if ship.readiness.is_ready)

    # Текущие рейсы (не завершённые) с фильтрацией
    voyage_filter = _get_choice_param(request, 'voyage_filter', {'current', 'overdue'}, 'current')
    now = timezone.now()
    current_voyages = filter_voyages_by_health(
        Voyage.objects.select_related('ship'),
        health_filter=voyage_filter,
        now=now,
    )

    # Сортировка рейсов
    voyage_sort = _get_choice_param(request, 'voyage_sort', {'date', 'criticality', 'ship_name'}, 'date')
    if voyage_sort == 'criticality':
        current_voyages = annotate_voyage_health_priority(current_voyages, now=now).order_by('criticality', '-start_date')
    elif voyage_sort == 'ship_name':
        current_voyages = current_voyages.order_by('ship__name', '-start_date')
    else:  # 'date' (default)
        current_voyages = current_voyages.order_by('-start_date')

    # Последние ремонты
    recent_maintenances = Maintenance.objects.all().order_by('-start_date')[:5]

    # Проблемные объекты
    problematic_report = get_system_problem_states()

    context = {
        'ships': filtered_ships,
        'total_ships': len(ships),
        'ships_at_sea': ships_at_sea,
        'ships_in_port': ships_in_port,
        'ships_under_repair': ships_under_repair,
        'ships_ready': ships_ready,
        'current_voyages': current_voyages,
        'recent_maintenances': recent_maintenances,
        'problematic': problematic_report.grouped,
        'problematic_summary': problematic_report.severity_summary,
        'problematic_total': problematic_report.total_issues,
        'page_title': 'Управление флотом',
        'ship_filter': ship_filter,
        'crew_filter': crew_filter,
        'ship_sort': ship_sort,
        'voyage_filter': voyage_filter,
        'voyage_sort': voyage_sort,
    }
    return render(request, 'fleet/dashboard.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew', 'dispatcher', 'engineer'])
def ship_detail(request, ship_id):
    """Страница детальной информации о судне"""
    ship = get_object_or_404(Ship, id=ship_id)
    ensure_default_ship_crew_requirements(ship.type)
    crew = CrewMember.objects.filter(assigned_ship=ship).select_related('user')
    ship = enrich_ship_for_operations(ship, crew_members=crew)
    voyages = Voyage.objects.filter(ship=ship).order_by('-start_date')
    maintenances = Maintenance.objects.filter(ship=ship).order_by('-start_date')

    context = {
        'ship': ship,
        'crew': crew,
        'voyages': voyages,
        'maintenances': maintenances,
        'page_title': f'Судно {ship.name}',
    }
    return render(request, 'fleet/ship_detail.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
def ship_crew_assign(request, ship_id):
    """Назначение состава экипажа на конкретное судно."""
    ship = get_object_or_404(Ship, id=ship_id)
    ensure_default_ship_crew_requirements(ship.type)
    current_crew = CrewMember.objects.filter(assigned_ship=ship).select_related('user').order_by('rank', 'user__last_name')
    available_crew = CrewMember.objects.filter(
        Q(assigned_ship=ship) | Q(assigned_ship__isnull=True)
    ).select_related('user').order_by('rank', 'user__last_name')

    readiness_snapshot = ship_readiness_service(ship, crew_members=current_crew, at_datetime=timezone.now())
    requirements = readiness_snapshot.context.get('crew_requirements', {})
    selected_crew_ids = list(current_crew.values_list('id', flat=True))

    if request.method == 'POST':
        selected_crew_ids = [int(item) for item in request.POST.getlist('crew_members') if item.isdigit()]
        selected_crew = list(
            CrewMember.objects.filter(id__in=selected_crew_ids)
            .filter(Q(assigned_ship=ship) | Q(assigned_ship__isnull=True))
            .select_related('user')
            .order_by('rank', 'user__last_name')
        )
        readiness = ship_readiness_service(ship, crew_members=selected_crew, at_datetime=timezone.now())
        requirements = readiness.context.get('crew_requirements', {})
        if not readiness.is_valid:
            messages.error(request, 'Нельзя сохранить состав экипажа: ' + '; '.join(readiness.blocking_reasons))
        else:
            with transaction.atomic():
                CrewMember.objects.filter(assigned_ship=ship).exclude(id__in=selected_crew_ids).update(assigned_ship=None)
                CrewMember.objects.filter(id__in=selected_crew_ids).update(assigned_ship=ship)
            messages.success(request, f'Состав экипажа для судна {ship.name} обновлён.')
            return redirect('fleet:ship_detail', ship_id=ship.id)

    context = {
        'ship': ship,
        'available_crew': available_crew,
        'selected_crew_ids': selected_crew_ids,
        'requirements': requirements,
        'page_title': f'Состав экипажа: {ship.name}',
    }
    return render(request, 'fleet/ship_crew_assign.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
def ship_create(request):
    """Создание нового судна (только для fleet_manager и director)"""
    location_choices = [
        'Порт Владивосток',
        'Порт Находка',
        'Порт Петропавловск-Камчатский',
        'Порт Корсаков',
        'Порт Ванино',
        'Порт Магадан',
        'Порт Советская Гавань',
        'Порт Де-Кастри',
        'Порт Александровск-Сахалинский',
        'Порт Холмск',
        'Порт Невельск',
        'Порт Усть-Камчатск',
        'Охотское море',
        'Берингово море',
        'Японское море',
        'Тихий океан',
        'Атлантический океан',
        'Северный Ледовитый океан',
        'Индийский океан',
        'В море (рейс)',
        'В порту (ожидание)',
        'На ремонте',
        'Судоремонтный завод',
        'Судоремонтный завод №2',
        'Судоремонтный завод №3',
        'В доке',
        'На отстое',
        'В резерве',
    ]
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            imo = request.POST.get('imo')
            ship_type = request.POST.get('type', 'fishing')
            current_location = request.POST.get('current_location', '')
            technical_condition = request.POST.get('technical_condition', '')
            engine_hours = _parse_engine_hours(request.POST.get('engine_hours'))
            last_repair_date_raw = request.POST.get('last_repair_date', '')
            last_repair_date = _parse_local_date(last_repair_date_raw) if last_repair_date_raw else None
            # Валидация
            if not name or not imo:
                messages.error(request, 'Название и номер ИМО обязательны')
                return redirect('fleet:ship_create')
            if last_repair_date_raw and not last_repair_date:
                messages.error(request, 'Некорректная дата последнего ремонта')
                return redirect('fleet:ship_create')
            if Ship.objects.filter(imo_number=imo).exists():
                messages.error(request, 'Судно с таким номером ИМО уже существует')
                return redirect('fleet:ship_create')
            # Создание судна
            ship = Ship.objects.create(
                name=name,
                imo_number=imo,
                type=ship_type,
                status='in_port',  # по умолчанию в порту
                current_location=current_location,
                technical_condition=technical_condition,
                engine_hours=engine_hours,
                last_repair_date=last_repair_date
            )
            ensure_planned_maintenance_for_ship(ship)
            messages.success(request, f'Судно {ship.name} успешно создано.')
            return redirect('fleet:ship_detail', ship_id=ship.id)
        except ValidationError as e:
            messages.error(request, '; '.join(_validation_messages(e)))
            return redirect('fleet:ship_create')
        except Exception as e:
            messages.error(request, f'Ошибка при создании судна: {e}')
            return redirect('fleet:ship_create')
    context = {
        'page_title': 'Добавить судно',
        'location_choices': location_choices,
        'date_input_placeholder': DATE_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/ship_form.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
def ship_edit(request, ship_id):
    """Редактирование судна"""
    ship = get_object_or_404(Ship, id=ship_id)
    location_choices = [
        'Порт Владивосток',
        'Порт Находка',
        'Порт Петропавловск-Камчатский',
        'Порт Корсаков',
        'Порт Ванино',
        'Порт Магадан',
        'Порт Советская Гавань',
        'Порт Де-Кастри',
        'Порт Александровск-Сахалинский',
        'Порт Холмск',
        'Порт Невельск',
        'Порт Усть-Камчатск',
        'Охотское море',
        'Берингово море',
        'Японское море',
        'Тихий океан',
        'Атлантический океан',
        'Северный Ледовитый океан',
        'Индийский океан',
        'В море (рейс)',
        'В порту (ожидание)',
        'На ремонте',
        'Судоремонтный завод',
        'Судоремонтный завод №2',
        'Судоремонтный завод №3',
        'В доке',
        'На отстое',
        'В резерве',
    ]
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            imo = request.POST.get('imo')
            ship_type = request.POST.get('type', 'fishing')
            current_location = request.POST.get('current_location', '')
            technical_condition = request.POST.get('technical_condition', '')
            # Валидация
            if not name or not imo:
                messages.error(request, 'Название и номер ИМО обязательны')
                return redirect('fleet:ship_edit', ship_id=ship.id)
            # Проверка уникальности IMO (если изменился)
            if imo != ship.imo_number and Ship.objects.filter(imo_number=imo).exists():
                messages.error(request, 'Судно с таким номером ИМО уже существует')
                return redirect('fleet:ship_edit', ship_id=ship.id)
            # Обновление полей
            ship.name = name
            ship.imo_number = imo
            ship.type = ship_type
            ship.current_location = current_location
            ship.technical_condition = technical_condition
            ship.engine_hours = _parse_engine_hours(request.POST.get('engine_hours'))
            last_repair_date_raw = request.POST.get('last_repair_date', '')
            ship.last_repair_date = _parse_local_date(last_repair_date_raw) if last_repair_date_raw else None
            if last_repair_date_raw and ship.last_repair_date is None:
                raise ValidationError('Некорректная дата последнего ремонта')
            ship.save()
            messages.success(request, f'Судно {ship.name} успешно обновлено.')
            return redirect('fleet:ship_detail', ship_id=ship.id)
        except ValidationError as e:
            messages.error(request, '; '.join(_validation_messages(e)))
            return redirect('fleet:ship_edit', ship_id=ship.id)
        except Exception as e:
            messages.error(request, f'Ошибка при обновлении судна: {e}')
            return redirect('fleet:ship_edit', ship_id=ship.id)
    context = {
        'ship': ship,
        'page_title': f'Редактировать {ship.name}',
        'location_choices': location_choices,
        'last_repair_date_value': _format_date_input(ship.last_repair_date),
        'date_input_placeholder': DATE_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/ship_form.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'dispatcher'])
def voyage_create(request, ship_id=None):
    """Создание нового рейса"""
    ship = None
    if ship_id:
        ship = get_object_or_404(Ship, id=ship_id)
        ship = enrich_ship_for_operations(ship)
    ships = Ship.objects.all().order_by('name')
    crew_members = CrewMember.objects.all().select_related('user').order_by('user__last_name')
    if request.method == 'POST':
        try:
            ship_id = request.POST.get('ship')
            if not ship_id:
                messages.error(request, 'Не выбрано судно')
                return redirect('fleet:voyage_create')
            ship = Ship.objects.get(id=ship_id)
            start_date_str = request.POST.get('start_date')
            end_date_str = request.POST.get('end_date')
            fishing_area = request.POST.get('fishing_area', 'okhotsk')
            catch_plan = float(request.POST.get('catch_plan', 0))
            start_date = _parse_local_datetime(start_date_str)
            if not start_date:
                start_date = timezone.now()
            end_date = _parse_local_datetime(end_date_str) if end_date_str else None
            # Преобразуем наивные datetime в aware, если необходимо
            if start_date and timezone.is_naive(start_date):
                start_date = timezone.make_aware(start_date)
            if end_date and timezone.is_naive(end_date):
                end_date = timezone.make_aware(end_date)

            crew_ids = request.POST.getlist('crew')
            selected_crew = list(CrewMember.objects.filter(id__in=crew_ids).select_related('user'))
            readiness_crew = selected_crew or list(CrewMember.objects.filter(assigned_ship=ship).select_related('user'))
            readiness = ship_readiness_service(ship, crew_members=readiness_crew, at_datetime=start_date)
            if not readiness.is_ready:
                messages.error(
                    request,
                    'Судно не готово к рейсу: ' + '; '.join(readiness.blocking_reasons)
                )
                return redirect('fleet:voyage_create')

            temp_voyage = Voyage(
                ship=ship,
                start_date=start_date,
                end_date=end_date,
                is_completed=False
            )
            try:
                temp_voyage.clean()
            except ValidationError as e:
                error_messages = _validation_messages(e)
                if error_messages:
                    messages.error(request, 'Ошибка валидации: ' + '; '.join(error_messages))
                else:
                    messages.error(request, f'Ошибка валидации: {e}')
                return redirect('fleet:voyage_create')

            crew = CrewMember.objects.filter(id__in=crew_ids).select_related('user')
            crew_contract_validation = validate_crew_for_voyage_period(
                crew,
                voyage_start_date=start_date,
                planned_return_date=end_date,
            )
            if not crew_contract_validation.is_valid:
                messages.error(
                    request,
                    'Ошибка проверки контрактов экипажа: ' + '; '.join(crew_contract_validation.blocking_reasons)
                )
                return redirect('fleet:voyage_create')
            overlapping_crew = _find_overlapping_crew(
                crew,
                start_date=start_date,
                end_date=end_date,
            )
            if overlapping_crew:
                names = ', '.join([c.user.get_full_name() or c.user.username for c in overlapping_crew])
                messages.warning(
                    request,
                    f'Следующие члены экипажа уже заняты в других рейсах в указанный период: {names}. '
                    'Рейс не создан.'
                )
                return redirect('fleet:voyage_create')
            for warning in crew_contract_validation.warnings:
                messages.warning(request, warning)

            with transaction.atomic():
                voyage = Voyage.objects.create(
                    ship=ship,
                    start_date=start_date,
                    end_date=end_date,
                    fishing_area=fishing_area,
                    catch_plan=catch_plan,
                    is_completed=False,
                )
                if crew_ids:
                    voyage.crew.set(crew)
            messages.success(request, f'Рейс для судна {ship.name} успешно создан.')
            return redirect('fleet:fleet_dashboard')
        except Exception as e:
            messages.error(request, f'Ошибка при создании рейса: {e}')
            return redirect('fleet:voyage_create')
    context = {
        'ship': ship,
        'ships': [enrich_ship_for_operations(item) for item in ships],
        'crew_members': crew_members,
        'selected_crew_ids': [],
        'page_title': 'Создать рейс',
        'start_date_value': request.POST.get('start_date', ''),
        'end_date_value': request.POST.get('end_date', ''),
        'datetime_input_placeholder': DATETIME_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/voyage_form.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'dispatcher'])
def voyage_edit(request, voyage_id):
    """Редактирование рейса"""
    voyage = get_object_or_404(Voyage, id=voyage_id)
    ships = Ship.objects.all().order_by('name')
    crew_members = CrewMember.objects.all().select_related('user').order_by('user__last_name')
    if request.method == 'POST':
        try:
            ship_id = request.POST.get('ship')
            if not ship_id:
                messages.error(request, 'Не выбрано судно')
                return redirect('fleet:voyage_edit', voyage_id=voyage.id)
            ship = Ship.objects.get(id=ship_id)
            start_date_str = request.POST.get('start_date')
            end_date_str = request.POST.get('end_date')
            fishing_area = request.POST.get('fishing_area', 'okhotsk')
            catch_plan = float(request.POST.get('catch_plan', 0))
            start_date = _parse_local_datetime(start_date_str)
            if not start_date:
                start_date = timezone.now()
            end_date = _parse_local_datetime(end_date_str) if end_date_str else None
            # Преобразуем наивные datetime в aware, если необходимо
            if start_date and timezone.is_naive(start_date):
                start_date = timezone.make_aware(start_date)
            if end_date and timezone.is_naive(end_date):
                end_date = timezone.make_aware(end_date)

            crew_ids = request.POST.getlist('crew')
            selected_crew = list(CrewMember.objects.filter(id__in=crew_ids).select_related('user'))
            readiness_crew = selected_crew or list(CrewMember.objects.filter(assigned_ship=ship).select_related('user'))
            readiness = ship_readiness_service(
                ship,
                crew_members=readiness_crew,
                at_datetime=start_date,
                exclude_voyage_id=voyage.id,
            )
            if not readiness.is_ready:
                messages.error(
                    request,
                    'Судно не готово к рейсу: ' + '; '.join(readiness.blocking_reasons)
                )
                return redirect('fleet:voyage_edit', voyage_id=voyage.id)

            # Временно обновляем поля voyage для проверки
            original_ship = voyage.ship
            original_start = voyage.start_date
            original_end = voyage.end_date
            voyage.ship = ship
            voyage.start_date = start_date
            voyage.end_date = end_date
            try:
                voyage.clean()
            except ValidationError as e:
                # Восстанавливаем оригинальные значения
                voyage.ship = original_ship
                voyage.start_date = original_start
                voyage.end_date = original_end
                error_messages = _validation_messages(e)
                if error_messages:
                    messages.error(request, 'Ошибка валидации: ' + '; '.join(error_messages))
                else:
                    messages.error(request, f'Ошибка валидации: {e}')
                return redirect('fleet:voyage_edit', voyage_id=voyage.id)
            # Если валидация прошла, проверяем экипаж
            crew = CrewMember.objects.filter(id__in=crew_ids).select_related('user')
            crew_contract_validation = validate_crew_for_voyage_period(
                crew,
                voyage_start_date=start_date,
                planned_return_date=end_date,
            )
            if not crew_contract_validation.is_valid:
                voyage.ship = original_ship
                voyage.start_date = original_start
                voyage.end_date = original_end
                messages.error(
                    request,
                    'Ошибка проверки контрактов экипажа: ' + '; '.join(crew_contract_validation.blocking_reasons)
                )
                return redirect('fleet:voyage_edit', voyage_id=voyage.id)
            overlapping_crew = _find_overlapping_crew(
                crew,
                start_date=start_date,
                end_date=end_date,
                exclude_voyage_id=voyage.id,
            )
            if overlapping_crew:
                voyage.ship = original_ship
                voyage.start_date = original_start
                voyage.end_date = original_end
                names = ', '.join([c.user.get_full_name() or c.user.username for c in overlapping_crew])
                messages.warning(
                    request,
                    f'Следующие члены экипажа уже заняты в других рейсах в указанный период: {names}. '
                    'Изменения не сохранены.'
                )
                return redirect('fleet:voyage_edit', voyage_id=voyage.id)
            for warning in crew_contract_validation.warnings:
                messages.warning(request, warning)
            # Если проверка экипажа прошла, сохраняем остальные поля
            with transaction.atomic():
                voyage.fishing_area = fishing_area
                voyage.catch_plan = catch_plan
                voyage.save()
                if crew_ids:
                    voyage.crew.set(crew)
                else:
                    voyage.crew.clear()
            messages.success(request, f'Рейс для судна {ship.name} успешно обновлён.')
            return redirect('fleet:fleet_dashboard')
        except Exception as e:
            messages.error(request, f'Ошибка при обновлении рейса: {e}')
            return redirect('fleet:voyage_edit', voyage_id=voyage.id)
    # Предзаполнение формы
    context = {
        'voyage': voyage,
        'ship': voyage.ship,
        'ships': ships,
        'crew_members': crew_members,
        'selected_crew_ids': list(voyage.crew.values_list('id', flat=True)),
        'page_title': 'Редактировать рейс',
        'start_date_value': _format_datetime_input(voyage.start_date),
        'end_date_value': _format_datetime_input(voyage.end_date),
        'datetime_input_placeholder': DATETIME_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/voyage_form.html', context)

@login_required
@role_required(['director', 'fleet_manager', 'dispatcher'])
def voyage_complete(request, voyage_id):
    """Завершение рейса с вводом фактической добычи"""
    voyage = get_object_or_404(Voyage, id=voyage_id)
    if request.method == 'POST':
        try:
            actual_catch_raw = request.POST.get('actual_catch', '')
            end_date_raw = request.POST.get('end_date', '')

            actual_catch = float(actual_catch_raw) if actual_catch_raw not in ['', None] else None
            end_date = _parse_local_datetime(end_date_raw) if end_date_raw else timezone.now()

            if timezone.is_naive(end_date):
                end_date = timezone.make_aware(end_date)

            if actual_catch is not None and actual_catch < 0:
                messages.error(request, 'Фактическая добыча не может быть отрицательной.')
                return redirect('fleet:voyage_complete', voyage_id=voyage.id)

            if end_date <= voyage.start_date:
                messages.error(request, 'Дата возвращения должна быть позже даты выхода.')
                return redirect('fleet:voyage_complete', voyage_id=voyage.id)

            with transaction.atomic():
                voyage.actual_catch = actual_catch
                voyage.end_date = end_date
                voyage.is_completed = True
                voyage.save()

            messages.success(request, f'Рейс завершён. Факт добычи: {voyage.actual_catch if voyage.actual_catch is not None else "—"} т')
            return redirect('fleet:ship_detail', ship_id=voyage.ship.id)
        except ValueError:
            messages.error(request, 'Проверьте значение фактической добычи.')
            return redirect('fleet:voyage_complete', voyage_id=voyage.id)
        except Exception as e:
            messages.error(request, f'Ошибка при завершении рейса: {e}')
            return redirect('fleet:voyage_complete', voyage_id=voyage.id)
    context = {
        'voyage': voyage,
        'page_title': 'Завершить рейс',
        'end_date_value': _format_datetime_input(voyage.end_date),
        'datetime_input_placeholder': DATETIME_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/voyage_complete.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew'])
def crew_list(request):
    """Список всего экипажа с фильтрацией по контракту и назначению."""
    from django.utils import timezone
    
    crew = CrewMember.objects.all().select_related('user', 'assigned_ship')
    contract_filter = request.GET.get('contract_status', 'all')
    assignment_filter = request.GET.get('assignment', 'all')
    today = timezone.now().date()

    if contract_filter == 'expired':
        crew = crew.filter(contract_end_date__lt=today)
    elif contract_filter == 'expires_7':
        crew = crew.filter(
            contract_end_date__gte=today,
            contract_end_date__lte=today + timezone.timedelta(days=7)
        )
    elif contract_filter == 'expires_30':
        crew = crew.filter(
            contract_end_date__gte=today + timezone.timedelta(days=8),
            contract_end_date__lte=today + timezone.timedelta(days=30)
        )
    elif contract_filter == 'active':
        crew = crew.filter(
            contract_end_date__gt=today + timezone.timedelta(days=30)
        )
    elif contract_filter == 'no_contract':
        crew = crew.filter(contract_end_date__isnull=True)

    if assignment_filter == 'reserve':
        crew = crew.filter(assigned_ship__isnull=True)
    elif assignment_filter == 'assigned':
        crew = crew.filter(assigned_ship__isnull=False)

    crew = crew.order_by('contract_end_date', 'rank', 'user__last_name')
    
    context = {
        'crew': crew,
        'page_title': 'Экипаж',
        'today': today,
        'contract_filter': contract_filter,
        'assignment_filter': assignment_filter,
    }
    return render(request, 'fleet/crew_list.html', context)
@login_required
@role_required(['director', 'fleet_manager', 'crew', 'engineer', 'dispatcher'])
def maintenance_list(request):
    """Список всех ремонтов"""
    
    # Фильтрация по проблемности
    maintenance_filter = _get_choice_param(request, 'maintenance_filter', {'all', 'overdue'}, 'all')
    today = timezone.now().date()
    maintenances = filter_maintenances_by_health(
        Maintenance.objects.all(),
        health_filter=maintenance_filter,
        today=today,
    )
    
    # Сортировка
    maintenance_sort = _get_choice_param(request, 'maintenance_sort', {'date', 'criticality', 'ship_name'}, 'date')
    from django.db.models import Case, When, IntegerField
    
    # Базовая сортировка по статусу (для порядка по умолчанию)
    order = Case(
        When(status='in_progress', then=0),
        When(status='planned', then=1),
        When(status='completed', then=2),
        default=3,
        output_field=IntegerField(),
    )
    maintenances = maintenances.annotate(custom_order=order)
    
    if maintenance_sort == 'criticality':
        # Критичность: overdue (blocking) -> too_long (warning) -> normal
        maintenances = annotate_maintenance_health_priority(maintenances, today=today).order_by('criticality', 'custom_order', '-start_date')
    elif maintenance_sort == 'ship_name':
        maintenances = maintenances.order_by('ship__name', 'custom_order', '-start_date')
    else:  # 'date' (default)
        maintenances = maintenances.order_by('custom_order', '-start_date')
    
    context = {
        'maintenances': maintenances,
        'page_title': 'Судоремонт',
        'maintenance_filter': maintenance_filter,
        'maintenance_sort': maintenance_sort,
    }
    return render(request, 'fleet/maintenance_list.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'engineer'])
def maintenance_create(request, ship_id=None):
    """Создание записи о ТО/ремонте"""
    ship = None
    if ship_id:
        ship = get_object_or_404(Ship, id=ship_id)
    ships = Ship.objects.all().order_by('name')
    contractors = Contractor.objects.all().order_by('name')
    ensure_default_repair_works()
    repair_works = RepairWork.objects.all().order_by('name')
    if request.method == 'POST':
        try:
            ship_id = request.POST.get('ship')
            if not ship_id:
                messages.error(request, 'Не выбрано судно')
                return redirect('fleet:maintenance_create')
            ship = Ship.objects.get(id=ship_id)
            maintenance_type = request.POST.get('maintenance_type', 'planned')
            description = request.POST.get('description', '')
            start_date_str = request.POST.get('start_date')
            end_date_str = request.POST.get('end_date')
            cost = request.POST.get('cost', 0)
            status = request.POST.get('status', 'planned')
            contractor_id = request.POST.get('contractor')
            contractor_text = request.POST.get('contractor_text', '')
            selected_work_ids = request.POST.getlist('repair_works')
            start_date = _parse_local_date(start_date_str) if start_date_str else timezone.now().date()
            end_date = _parse_local_date(end_date_str) if end_date_str else None
            # Определение контрагента
            contractor = None
            if contractor_id:
                try:
                    contractor = Contractor.objects.get(id=contractor_id)
                except Contractor.DoesNotExist:
                    pass
            maintenance = Maintenance(
                ship=ship,
                maintenance_type=maintenance_type,
                description=description,
                start_date=start_date,
                end_date=end_date,
                cost=cost,
                status=status,
                contractor=contractor,
                contractor_text=contractor_text,
            )
            try:
                maintenance.clean()
            except ValidationError as e:
                error_messages = _validation_messages(e)
                if error_messages:
                    messages.error(request, 'Ошибка валидации: ' + '; '.join(error_messages))
                else:
                    messages.error(request, f'Ошибка валидации: {e}')
                return redirect('fleet:maintenance_create')
            # Проверка пересечения с рейсами для аварийного ремонта (предупреждение)
            if maintenance_type == 'emergency':
                overlapping_voyages = _find_overlapping_voyages_for_maintenance(ship, start_date, end_date)
                if overlapping_voyages.exists():
                    messages.warning(
                        request,
                        f'Внимание! Судно находится в рейсе в указанный период. '
                        f'Требуется досрочное завершение рейса(ов): {", ".join(str(v) for v in overlapping_voyages[:3])}.'
                    )
            with transaction.atomic():
                maintenance.save()
                maintenance.repair_works.set(RepairWork.objects.filter(id__in=selected_work_ids))
            messages.success(request, f'Запись о ремонте для судна {ship.name} успешно создана.')
            return redirect('fleet:maintenance_list')
        except Exception as e:
            messages.error(request, f'Ошибка при создании записи: {e}')
            return redirect('fleet:maintenance_create')
    context = {
        'ship': ship,
        'ships': ships,
        'contractors': contractors,
        'repair_works': repair_works,
        'today': timezone.now().date(),
        'page_title': 'Добавить ТО/ремонт',
        'maintenance_type_choices': Maintenance.MAINTENANCE_TYPE_CHOICES,
        'status_choices': Maintenance.STATUS_CHOICES,
        'selected_work_ids': [],
        'start_date_value': _format_date_input(timezone.now().date()),
        'end_date_value': '',
        'date_input_placeholder': DATE_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/maintenance_form.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew', 'engineer', 'dispatcher'])
def maintenance_detail(request, maintenance_id):
    """Детальная информация о ремонте"""
    maintenance = get_object_or_404(Maintenance.objects.prefetch_related('repair_works'), id=maintenance_id)
    maintenance.ship = enrich_ship_for_operations(maintenance.ship)
    context = {
        'maintenance': maintenance,
        'page_title': f'Ремонт {maintenance.ship.name}',
    }
    return render(request, 'fleet/maintenance_detail.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'engineer'])
def maintenance_edit(request, maintenance_id):
    """Редактирование записи о ТО/ремонте"""
    maintenance = get_object_or_404(Maintenance.objects.prefetch_related('repair_works'), id=maintenance_id)
    ships = Ship.objects.all().order_by('name')
    contractors = Contractor.objects.all().order_by('name')
    ensure_default_repair_works()
    repair_works = RepairWork.objects.all().order_by('name')
    if request.method == 'POST':
        try:
            ship_id = request.POST.get('ship')
            if not ship_id:
                messages.error(request, 'Не выбрано судно')
                return redirect('fleet:maintenance_edit', maintenance_id=maintenance.id)
            ship = Ship.objects.get(id=ship_id)
            maintenance_type = request.POST.get('maintenance_type', 'planned')
            description = request.POST.get('description', '')
            start_date_str = request.POST.get('start_date')
            end_date_str = request.POST.get('end_date')
            cost = request.POST.get('cost', 0)
            status = request.POST.get('status', 'planned')
            contractor_id = request.POST.get('contractor')
            contractor_text = request.POST.get('contractor_text', '')
            selected_work_ids = request.POST.getlist('repair_works')
            start_date = _parse_local_date(start_date_str) if start_date_str else maintenance.start_date
            end_date = _parse_local_date(end_date_str) if end_date_str else None
            # Определение контрагента
            contractor = None
            if contractor_id:
                try:
                    contractor = Contractor.objects.get(id=contractor_id)
                except Contractor.DoesNotExist:
                    pass
            # Сохраняем оригинальные значения для восстановления в случае ошибки
            original_ship = maintenance.ship
            original_start = maintenance.start_date
            original_end = maintenance.end_date
            original_type = maintenance.maintenance_type
            # Временно обновляем поля для валидации
            maintenance.ship = ship
            maintenance.maintenance_type = maintenance_type
            maintenance.description = description
            maintenance.start_date = start_date
            maintenance.end_date = end_date
            maintenance.cost = cost
            maintenance.status = status
            maintenance.contractor = contractor
            maintenance.contractor_text = contractor_text
            try:
                maintenance.clean()
            except ValidationError as e:
                # Восстанавливаем оригинальные значения
                maintenance.ship = original_ship
                maintenance.start_date = original_start
                maintenance.end_date = original_end
                maintenance.maintenance_type = original_type
                error_messages = _validation_messages(e)
                if error_messages:
                    messages.error(request, 'Ошибка валидации: ' + '; '.join(error_messages))
                else:
                    messages.error(request, f'Ошибка валидации: {e}')
                return redirect('fleet:maintenance_edit', maintenance_id=maintenance.id)
            # Проверка пересечения с рейсами для аварийного ремонта (предупреждение)
            if maintenance_type == 'emergency':
                overlapping_voyages = _find_overlapping_voyages_for_maintenance(
                    ship,
                    start_date,
                    end_date,
                    exclude_maintenance_id=maintenance.id,
                )
                if overlapping_voyages.exists():
                    messages.warning(
                        request,
                        f'Внимание! Судно находится в рейсе в указанный период. '
                        f'Требуется досрочное завершение рейса(ов): {", ".join(str(v) for v in overlapping_voyages[:3])}.'
                    )
            with transaction.atomic():
                maintenance.save()
                maintenance.repair_works.set(RepairWork.objects.filter(id__in=selected_work_ids))
            messages.success(request, f'Запись о ремонте для судна {ship.name} успешно обновлена.')
            return redirect('fleet:maintenance_list')
        except Exception as e:
            messages.error(request, f'Ошибка при обновлении записи: {e}')
            return redirect('fleet:maintenance_edit', maintenance_id=maintenance.id)
    context = {
        'maintenance': maintenance,
        'ships': ships,
        'contractors': contractors,
        'repair_works': repair_works,
        'today': timezone.now().date(),
        'page_title': 'Редактировать ТО/ремонт',
        'maintenance_type_choices': Maintenance.MAINTENANCE_TYPE_CHOICES,
        'status_choices': Maintenance.STATUS_CHOICES,
        'selected_work_ids': list(maintenance.repair_works.values_list('id', flat=True)),
        'start_date_value': _format_date_input(maintenance.start_date),
        'end_date_value': _format_date_input(maintenance.end_date),
        'date_input_placeholder': DATE_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/maintenance_form.html', context)
@login_required
@role_required(['director', 'fleet_manager', 'engineer'])
def maintenance_complete(request, maintenance_id):
    """Завершение ремонта (установка статуса 'completed' и даты окончания)"""
    maintenance = get_object_or_404(Maintenance, id=maintenance_id)
    if request.method == 'POST':
        try:
            maintenance.status = 'completed'
            if not maintenance.end_date:
                maintenance.end_date = timezone.now().date()
            maintenance.save()
            messages.success(request, f'Ремонт для судна {maintenance.ship.name} отмечен как завершённый.')
            return redirect('fleet:maintenance_detail', maintenance_id=maintenance.id)
        except Exception as e:
            messages.error(request, f'Ошибка при завершении ремонта: {e}')
            return redirect('fleet:maintenance_detail', maintenance_id=maintenance.id)
    # Если GET, просто редиректим на детальную страницу
    return redirect('fleet:maintenance_detail', maintenance_id=maintenance.id)


@login_required
@role_required(['director', 'fleet_manager'])
def crew_create(request):
    """Создание нового члена экипажа"""
    from django.contrib.auth.models import User
    from accounts.models import UserRole
    ships = Ship.objects.all().order_by('name')
    if request.method == 'POST':
        try:
            username = request.POST.get('username')
            password = request.POST.get('password')
            email = request.POST.get('email')
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            patronymic = request.POST.get('patronymic', '')
            rank = request.POST.get('rank', 'seaman')
            contract_end_date_str = request.POST.get('contract_end_date')
            assigned_ship_id = request.POST.get('assigned_ship')
            assigned_ship = Ship.objects.get(id=assigned_ship_id) if assigned_ship_id else None
            contract_end_date = _parse_local_date(contract_end_date_str) if contract_end_date_str else None
            user = User.objects.create_user(
                username=username,
                password=password,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
            user_role, _ = UserRole.objects.get_or_create(user=user)
            user_role.role = _role_for_crew_rank(rank)
            user_role.save(update_fields=['role'])
            crew_member = CrewMember(
                user=user,
                rank=rank,
                assigned_ship=assigned_ship,
                contract_end_date=contract_end_date,
                notes=f'Отчество: {patronymic}' if patronymic else ''
            )
            crew_member.full_clean()
            crew_member.save()
            messages.success(request, f'Член экипажа {user.get_full_name()} успешно создан.')
            return redirect('fleet:crew_list')
        except Exception as e:
            messages.error(request, f'Ошибка при создании члена экипажа: {e}')
            return redirect('fleet:crew_create')
    context = {
        'ships': ships,
        'page_title': 'Добавить члена экипажа',
        'rank_choices': CrewMember.RANK_CHOICES,
        'crew_member': None,
        'patronymic': '',
        'contract_end_date_value': '',
        'date_input_placeholder': DATE_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/crew_form.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
def crew_edit(request, crew_id):
    """Редактирование сотрудника экипажа и его назначения на судно."""
    crew_member = get_object_or_404(CrewMember.objects.select_related('user', 'assigned_ship'), id=crew_id)
    ships = Ship.objects.all().order_by('name')

    if request.method == 'POST':
        try:
            user = crew_member.user
            user.email = request.POST.get('email', '')
            user.first_name = request.POST.get('first_name', '')
            user.last_name = request.POST.get('last_name', '')
            password = request.POST.get('password', '')
            if password:
                user.set_password(password)
            user.save()

            patronymic = request.POST.get('patronymic', '')
            contract_end_date_str = request.POST.get('contract_end_date')
            assigned_ship_id = request.POST.get('assigned_ship')

            crew_member.rank = request.POST.get('rank', crew_member.rank)
            crew_member.assigned_ship = Ship.objects.get(id=assigned_ship_id) if assigned_ship_id else None
            crew_member.contract_end_date = _parse_local_date(contract_end_date_str) if contract_end_date_str else None
            crew_member.notes = f'Отчество: {patronymic}' if patronymic else ''
            crew_member.full_clean()
            crew_member.save()
            _sync_user_role_with_crew_rank(user, crew_member.rank)

            messages.success(request, f'Данные сотрудника {user.get_full_name() or user.username} обновлены.')
            return redirect('fleet:crew_list')
        except Exception as e:
            messages.error(request, f'Ошибка при обновлении сотрудника: {e}')
            return redirect('fleet:crew_edit', crew_id=crew_member.id)

    patronymic = ''
    if crew_member.notes.startswith('Отчество: '):
        patronymic = crew_member.notes.replace('Отчество: ', '', 1)

    context = {
        'ships': ships,
        'page_title': 'Назначить или изменить члена экипажа',
        'rank_choices': CrewMember.RANK_CHOICES,
        'crew_member': crew_member,
        'patronymic': patronymic,
        'contract_end_date_value': _format_date_input(crew_member.contract_end_date),
        'date_input_placeholder': DATE_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/crew_form.html', context)

@login_required
@role_required(['director', 'fleet_manager'])
def contractor_list(request):
    """Список всех контрагентов"""
    contractors = Contractor.objects.all().order_by('name')
    context = {
        'contractors': contractors,
        'page_title': 'Контрагенты',
    }
    return render(request, 'fleet/contractor_list.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
def contractor_create(request):
    """Создание нового контрагента"""
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            contact_person = request.POST.get('contact_person', '')
            phone = request.POST.get('phone', '')
            email = request.POST.get('email', '')
            specialization = request.POST.get('specialization', '')
            notes = request.POST.get('notes', '')
            if not name:
                messages.error(request, 'Название контрагента обязательно')
                return redirect('fleet:contractor_create')
            contractor = Contractor.objects.create(
                name=name,
                contact_person=contact_person,
                phone=phone,
                email=email,
                specialization=specialization,
                notes=notes,
            )
            messages.success(request, f'Контрагент {contractor.name} успешно создан.')
            return redirect('fleet:contractor_list')
        except Exception as e:
            messages.error(request, f'Ошибка при создании контрагента: {e}')
            return redirect('fleet:contractor_create')
    context = {
        'page_title': 'Добавить контрагента',
    }
    return render(request, 'fleet/contractor_form.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
def contractor_edit(request, contractor_id):
    """Редактирование контрагента"""
    contractor = get_object_or_404(Contractor, id=contractor_id)
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            contact_person = request.POST.get('contact_person', '')
            phone = request.POST.get('phone', '')
            email = request.POST.get('email', '')
            specialization = request.POST.get('specialization', '')
            notes = request.POST.get('notes', '')
            if not name:
                messages.error(request, 'Название контрагента обязательно')
                return redirect('fleet:contractor_edit', contractor_id=contractor.id)
            contractor.name = name
            contractor.contact_person = contact_person
            contractor.phone = phone
            contractor.email = email
            contractor.specialization = specialization
            contractor.notes = notes
            contractor.save()
            messages.success(request, f'Контрагент {contractor.name} успешно обновлён.')
            return redirect('fleet:contractor_list')
        except Exception as e:
            messages.error(request, f'Ошибка при обновлении контрагента: {e}')
            return redirect('fleet:contractor_edit', contractor_id=contractor.id)
    context = {
        'contractor': contractor,
        'page_title': f'Редактировать {contractor.name}',
    }
    return render(request, 'fleet/contractor_form.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
def contractor_delete(request, contractor_id):
    """Удаление контрагента"""
    contractor = get_object_or_404(Contractor, id=contractor_id)
    if request.method == 'POST':
        try:
            contractor_name = contractor.name
            contractor.delete()
            messages.success(request, f'Контрагент {contractor_name} удалён.')
            return redirect('fleet:contractor_list')
        except Exception as e:
            messages.error(request, f'Ошибка при удалении контрагента: {e}')
            return redirect('fleet:contractor_list')
    # Если GET, показываем страницу подтверждения
    context = {
        'contractor': contractor,
        'page_title': 'Удалить контрагента',
    }
    return render(request, 'fleet/contractor_confirm_delete.html', context)

@login_required
@role_required(['director', 'fleet_manager', 'engineer', 'dispatcher'])
def maintenance_report(request):
    """График ремонтов и ТО - отчёт с фильтрацией по датам"""
    from django.utils.dateparse import parse_date
    from datetime import timedelta

    # Получаем параметры фильтрации
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    status_filter = request.GET.get('status', '')  # planned, in_progress, completed

    # Определяем диапазон дат по умолчанию: последние 30 дней
    default_start = timezone.now().date() - timedelta(days=30)
    default_end = timezone.now().date()

    start_date = _parse_local_date(start_date_str) if start_date_str else default_start
    end_date = _parse_local_date(end_date_str) if end_date_str else default_end

    # Базовый запрос
    queryset = Maintenance.objects.all()

    # Фильтрация по дате начала (start_date в диапазоне)
    if start_date:
        queryset = queryset.filter(start_date__gte=start_date)
    if end_date:
        queryset = queryset.filter(start_date__lte=end_date)

    # Фильтрация по статусу
    if status_filter in ['planned', 'in_progress', 'completed']:
        queryset = queryset.filter(status=status_filter)
    else:
        # По умолчанию показываем запланированные и в работе
        queryset = queryset.filter(status__in=['planned', 'in_progress'])

    # Сортировка по дате начала
    queryset = queryset.order_by('start_date')

    context = {
        'maintenances': queryset,
        'start_date': start_date,
        'end_date': end_date,
        'status_filter': status_filter,
        'page_title': 'График ремонтов и ТО',
        'start_date_value': _format_date_input(start_date),
        'end_date_value': _format_date_input(end_date),
        'date_input_placeholder': DATE_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/maintenance_report.html', context)
@login_required
@role_required(['director', 'fleet_manager', 'dispatcher', 'engineer'])
def fleet_performance_report(request):
    """Отчет по наработке флота за период"""
    from django.utils.dateparse import parse_date
    from datetime import timedelta
    from django.db.models import Count, Sum, F, ExpressionWrapper, DurationField
    from django.db.models.functions import ExtractMonth, ExtractQuarter

    # Параметры фильтрации
    period_type = request.GET.get('period', 'month')  # month, quarter, custom
    year = request.GET.get('year', timezone.now().year)
    month = request.GET.get('month', timezone.now().month)
    quarter = request.GET.get('quarter', 1)
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    # Определение дат периода
    if start_date_str and end_date_str:
        start_date = _parse_local_date(start_date_str)
        end_date = _parse_local_date(end_date_str)
    else:
        # По умолчанию текущий месяц
        start_date = timezone.now().replace(day=1).date()
        end_date = (start_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    # Выборка рейсов за период (завершённые)
    voyages = Voyage.objects.filter(
        is_completed=True,
        start_date__gte=start_date,
        end_date__lte=end_date
    ).select_related('ship')

    # Аннотация данных по судам
    from django.db.models import FloatField
    from django.db.models.functions import Cast

    performance_data = []
    ships = Ship.objects.all()
    total_voyages = 0
    total_catch = 0.0
    total_days = 0
    for ship in ships:
        ship_voyages = voyages.filter(ship=ship)
        voyage_count = ship_voyages.count()
        ship_catch = ship_voyages.aggregate(Sum('actual_catch'))['actual_catch__sum'] or 0
        # Вычисление общего времени в море (в днях)
        ship_days = 0
        for v in ship_voyages:
            if v.start_date and v.end_date:
                delta = v.end_date - v.start_date
                ship_days += delta.days
        performance_data.append({
            'ship': enrich_ship_for_operations(ship),
            'voyage_count': voyage_count,
            'total_catch': ship_catch,
            'total_days': ship_days,
        })
        total_voyages += voyage_count
        total_catch += ship_catch
        total_days += ship_days

    context = {
        'performance_data': performance_data,
        'start_date': start_date,
        'end_date': end_date,
        'period_type': period_type,
        'year': year,
        'month': month,
        'quarter': quarter,
        'page_title': 'Отчет по наработке флота',
        'total_voyages': total_voyages,
        'total_catch': total_catch,
        'total_days': total_days,
        'current_date': timezone.now(),
        'start_date_value': _format_date_input(start_date),
        'end_date_value': _format_date_input(end_date),
        'date_input_placeholder': DATE_INPUT_PLACEHOLDER,
    }
    return render(request, 'fleet/performance_report.html', context)

@login_required
@role_required(['director', 'fleet_manager', 'dispatcher', 'crew'])
def crew_manifest(request):
    """Посадочная ведомость экипажа по судам"""
    from django.utils import timezone
    ship_id = request.GET.get('ship_id')
    if ship_id:
        ship = get_object_or_404(Ship, id=ship_id)
        crew = CrewMember.objects.filter(assigned_ship=ship).select_related('user').order_by('rank')
    else:
        ship = None
        crew = CrewMember.objects.all().select_related('user', 'assigned_ship').order_by('assigned_ship__name', 'rank')

    # Форматирование данных для таблицы
    manifest_data = []
    for member in crew:
        # Определяем дату для отображения: contract_end_date, если есть, иначе date_joined
        date_display = '—'
        if member.contract_end_date:
            date_display = member.contract_end_date.strftime('%d.%m.%Y')
        elif member.user.date_joined:
            date_display = member.user.date_joined.strftime('%d.%m.%Y')
        manifest_data.append({
            'ship': member.assigned_ship.name if member.assigned_ship else 'Не назначено',
            'full_name': member.user.get_full_name(),
            'rank': member.get_rank_display(),
            'assignment_date': date_display,
            'user': member.user,
        })

    context = {
        'manifest_data': manifest_data,
        'ship': ship,
        'ships': Ship.objects.all(),
        'page_title': 'Посадочная ведомость',
        'current_date': timezone.now(),
    }
    return render(request, 'fleet/crew_manifest.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew', 'dispatcher', 'engineer'])
def notification_detail(request, notification_id):
    """Детальная информация об уведомлении"""
    notification = get_object_or_404(Notification, id=notification_id)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read', 'updated_at'])
    context = {
        'notification': notification,
        'page_title': f'Уведомление: {notification.title}',
    }
    return render(request, 'fleet/notification_detail.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew', 'dispatcher', 'engineer'])
def notifications_list(request):
    """Список всех уведомлений с фильтрами и сортировкой"""
    notifications = Notification.objects.all()
    
    # Фильтры
    filter_param = request.GET.get('filter', 'all')
    severity_param = request.GET.get('severity')
    
    if filter_param == 'active':
        notifications = notifications.filter(is_resolved=False)
    elif filter_param == 'resolved':
        notifications = notifications.filter(is_resolved=True)
    elif filter_param == 'unread':
        notifications = notifications.filter(is_read=False)
    
    if severity_param in ['blocking', 'warning', 'info']:
        notifications = notifications.filter(severity=severity_param)
    
    # Сортировка
    sort_param = request.GET.get('sort', 'newest')
    if sort_param == 'oldest':
        notifications = notifications.order_by('created_at')
    else:  # newest (default)
        notifications = notifications.order_by('is_resolved', '-created_at')
    
    unread_count = Notification.objects.filter(is_read=False).count()
    
    context = {
        'notifications': notifications,
        'unread_count': unread_count,
        'filter_param': filter_param,
        'severity_param': severity_param,
        'sort_param': sort_param,
        'page_title': 'Уведомления',
    }
    return render(request, 'fleet/notifications_list.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'captain', 'dispatcher', 'engineer'])
def maintenance_log_list(request, ship_id):
    """Страница журнала технических записей судна"""
    if _get_user_role(request.user) == 'captain':
        assigned_ship_id = _get_captain_assigned_ship_id(request.user)
        if assigned_ship_id != ship_id:
            raise PermissionDenied('Капитан может просматривать журнал только своего судна.')
    ship = get_object_or_404(Ship, id=ship_id)
    logs = MaintenanceLog.objects.filter(ship=ship).select_related('author').order_by('-recorded_at')
    
    context = {
        'ship': ship,
        'logs': logs,
        'page_title': f'Журнал технических записей - {ship.name}',
    }
    return render(request, 'fleet/maintenance_log_list.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'dispatcher', 'engineer'])
def captain_messages_list(request):
    ship_id = request.GET.get('ship_id')
    logs = MaintenanceLog.objects.select_related('ship', 'author').order_by('-recorded_at', '-id')
    ships = Ship.objects.all().order_by('name')

    if ship_id:
        logs = logs.filter(ship_id=ship_id)

    context = {
        'logs': logs[:100],
        'ships': ships,
        'selected_ship_id': int(ship_id) if ship_id and ship_id.isdigit() else None,
        'page_title': 'Сообщения капитанов',
    }
    return render(request, 'fleet/captain_messages_list.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew', 'dispatcher', 'engineer'])
def fleet_health_report(request):
    """Отчёт "Состояние флота" (Fleet Health Report)"""
    from django.utils import timezone
    now = timezone.now()
    today = now.date()
    
    # Получаем системные проблемы - это уже включает обогащённые суда
    problematic_report = get_system_problem_states()
    
    # Берём уже обогащённые суда из сервисного слоя, с безопасным fallback
    enriched_ships_from_issues = getattr(problematic_report, 'enriched_ships', {})
    if not enriched_ships_from_issues:
        for issue in problematic_report.issues:
            if issue.category == 'ships' and hasattr(issue.object, 'name'):
                enriched = issue.context.get('enriched')
                if enriched:
                    enriched_ships_from_issues[issue.object.id] = enriched
    
    # Получаем все суда и обогащаем их, используя уже обогащённые где возможно
    ships_queryset = Ship.objects.all().prefetch_related('crew_members__user', 'voyages', 'maintenances__contractor').order_by('name')
    ships = []
    for ship in ships_queryset:
        if ship.id in enriched_ships_from_issues:
            ships.append(enriched_ships_from_issues[ship.id])
        else:
            ships.append(enrich_ship_for_operations(ship))
    
    # Инициализация счетчиков
    total_ships = len(ships)
    ships_ready = 0
    ships_with_warning = 0
    ships_crew_shortage = 0
    ships_crew_unconfigured = 0
    ships_crew_complete = 0
    ships_at_sea = 0
    ships_in_port = 0
    ships_under_repair = 0
    ships_out_of_service = 0
    
    # Структуры для группировки проблем по судам
    ships_blocking_issues = {}
    ships_warning_issues = {}
    
    # Один проход по судам для всех метрик
    for ship in ships:
        # Готовность
        if ship.readiness.is_ready:
            ships_ready += 1
            if ship.readiness.severity_summary.get('warning', 0) > 0:
                ships_with_warning += 1
        else:
            # Собираем blocking issues для этого судна из readiness
            blocking_reasons = ship.readiness.blocking_reasons
            if blocking_reasons:
                ships_blocking_issues[ship.id] = {
                    'ship': ship,
                    'reasons': blocking_reasons[:2],  # максимум 2 причины
                    'details': [],
                    'total_issues': len(blocking_reasons),
                }
        
        # Статус экипажа
        crew_req = ship.readiness.context.get('crew_requirements', {})
        requirements_configured = crew_req.get('requirements_configured', False)
        missing_roles = crew_req.get('missing_roles', [])
        if not requirements_configured:
            ships_crew_unconfigured += 1
        elif missing_roles:
            ships_crew_shortage += 1
        else:
            ships_crew_complete += 1
        
        # Статус судна
        status = ship.actual_status_code
        if status == 'at_sea':
            ships_at_sea += 1
        elif status == 'in_port':
            ships_in_port += 1
        elif status == 'under_repair':
            ships_under_repair += 1
        elif status == 'out_of_service':
            ships_out_of_service += 1
    
    ships_not_ready = total_ships - ships_ready
    
    # Судна с warning (готовые, но есть warning) - уже посчитали ships_with_warning
    # Для детализации warning issues используем problematic_report
    warning_problems = [issue for issue in problematic_report.issues if issue.severity == 'warning']
    ships_warning_details = {}
    for issue in warning_problems:
        if issue.category == 'ships' and hasattr(issue.object, 'name'):
            ship_id = issue.object.id
            if ship_id not in ships_warning_details:
                ships_warning_details[ship_id] = {
                    'ship': issue.object,
                    'reasons': [],
                    'details': [],
                    'total_issues': 0,
                }
            ships_warning_details[ship_id]['reasons'].append(issue.reason)
            if issue.details:
                ships_warning_details[ship_id]['details'].append(issue.details)
            ships_warning_details[ship_id]['total_issues'] += 1
    
    # Ограничиваем количество причин для отображения
    ships_with_warning_list = []
    for data in ships_warning_details.values():
        data['reasons'] = data['reasons'][:2]
        data['details'] = data['details'][:2] if data['details'] else []
        ships_with_warning_list.append(data)
    
    # Судна с blocking проблемами (уже собраны из readiness)
    ships_with_blocking = list(ships_blocking_issues.values())
    
    # Проблемы по экипажу (crew-related blocking issues)
    crew_problems = []
    crew_ship_ids = set()
    blocking_problems = [issue for issue in problematic_report.issues if issue.severity == 'blocking']
    for issue in blocking_problems:
        if issue.category == 'ships' and hasattr(issue.object, 'name'):
            # Проверяем, является ли проблема crew-related
            if any(keyword in issue.reason.lower() for keyword in ['экипаж', 'crew', 'не хватает', 'состав', 'требован', 'contract']):
                crew_problems.append({
                    'ship': issue.object,
                    'reason': issue.reason,
                    'details': issue.details,
                })
                crew_ship_ids.add(issue.object.id)
    
    # Если в readiness есть crew problems, но их нет в crew-related blocking issues, добавляем их
    for ship in ships:
        crew_req = ship.readiness.context.get('crew_requirements', {})
        missing_roles = crew_req.get('missing_roles', [])
        if missing_roles and ship.id not in crew_ship_ids:
            crew_problems.append({
                'ship': ship,
                'missing_roles': missing_roles,
            })
    
    # Рейсы и ремонты с проблемами опираются на канонические code сервисного слоя
    voyage_problem_groups = group_problem_issues_by_code(problematic_report.issues, 'voyages')
    maintenance_problem_groups = group_problem_issues_by_code(problematic_report.issues, 'maintenances')
    overdue_voyages = voyage_problem_groups.get('voyage_overdue', [])
    too_long_voyages = voyage_problem_groups.get('voyage_too_long', [])
    overdue_maintenances = maintenance_problem_groups.get('maintenance_overdue_start', [])
    too_long_maintenances = maintenance_problem_groups.get('maintenance_too_long', [])
    
    # KPI (проценты)
    ready_percentage = round((ships_ready / total_ships * 100)) if total_ships > 0 else 0
    crew_shortage_percentage = round((ships_crew_shortage / total_ships * 100)) if total_ships > 0 else 0
    
    # Состояние рейсов (агрегировано)
    voyages_active = Voyage.objects.filter(is_completed=False).count()
    voyages_overdue = len(overdue_voyages)
    voyages_too_long = len(too_long_voyages)
    
    # Состояние ремонтов
    maintenances_in_progress = Maintenance.objects.filter(status='in_progress').count()
    maintenances_overdue = len(overdue_maintenances)
    maintenances_too_long = len(too_long_maintenances)
    
    context = {
        'page_title': 'Отчёт "Состояние флота"',
        'total_ships': total_ships,
        'ships_ready': ships_ready,
        'ships_not_ready': ships_not_ready,
        'ships_with_warning': ships_with_warning,
        'ships_crew_shortage': ships_crew_shortage,
        'ships_crew_unconfigured': ships_crew_unconfigured,
        'ships_crew_complete': ships_crew_complete,
        'ships_at_sea': ships_at_sea,
        'ships_in_port': ships_in_port,
        'ships_under_repair': ships_under_repair,
        'ships_out_of_service': ships_out_of_service,
        'ships_with_blocking': ships_with_blocking,
        'ships_with_warning_list': ships_with_warning_list,
        'crew_problems': crew_problems,
        'overdue_voyages': overdue_voyages,
        'too_long_voyages': too_long_voyages,
        'overdue_maintenances': overdue_maintenances,
        'too_long_maintenances': too_long_maintenances,
        'current_date': now,
        # KPI
        'ready_percentage': ready_percentage,
        'crew_shortage_percentage': crew_shortage_percentage,
        # Состояние рейсов
        'voyages_active': voyages_active,
        'voyages_overdue': voyages_overdue,
        'voyages_too_long': voyages_too_long,
        # Состояние ремонтов
        'maintenances_in_progress': maintenances_in_progress,
        'maintenances_overdue': maintenances_overdue,
        'maintenances_too_long': maintenances_too_long,
    }
    return render(request, 'fleet/fleet_health_report.html', context)
