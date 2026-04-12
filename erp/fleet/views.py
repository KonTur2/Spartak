from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum, Q, Case, When, IntegerField
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from accounts.decorators import role_required
from .models import Notification, Ship, CrewMember, Voyage, Maintenance, Contractor
from .services import (
    annotate_maintenance_health_priority,
    annotate_voyage_health_priority,
    enrich_ship_for_operations,
    filter_maintenances_by_health,
    filter_voyages_by_health,
    get_system_problem_states,
    ship_readiness_service,
    validate_crew_for_voyage_period,
)


OPEN_VOYAGE_DELTA = timezone.timedelta(days=3650)
OPEN_MAINTENANCE_DELTA = timezone.timedelta(days=3650)

def _validation_messages(error):
    if hasattr(error, 'error_dict'):
        return [str(item) for errors in error.error_dict.values() for item in errors]
    if hasattr(error, 'messages'):
        return [str(message) for message in error.messages]
    return [str(error)]


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
            ship.crew_status_class = 'bg-secondary'  # серый
        elif missing_roles:
            ship.crew_status = 'shortage'
            ship.crew_status_display = 'есть нехватка ролей'
            ship.crew_status_class = 'bg-danger'  # красный
        else:
            ship.crew_status = 'complete'
            ship.crew_status_display = 'состав укомплектован'
            ship.crew_status_class = 'bg-success'  # зеленый
    
    # Фильтрация судов по готовности
    ship_filter = request.GET.get('ship_filter', 'all')
    filtered_ships = ships
    if ship_filter == 'ready':
        filtered_ships = [ship for ship in ships if ship.readiness.is_ready]
    elif ship_filter == 'blocking':
        filtered_ships = [ship for ship in ships if not ship.readiness.is_ready]
    elif ship_filter == 'warning':
        filtered_ships = [ship for ship in ships if ship.readiness.is_ready and ship.readiness.severity_summary.get('warning', 0) > 0]
    # 'all' - без фильтрации
    
    # Фильтрация судов по статусу состава экипажа
    crew_filter = request.GET.get('crew_filter', 'all')
    if crew_filter == 'complete':
        filtered_ships = [ship for ship in filtered_ships if ship.crew_status == 'complete']
    elif crew_filter == 'shortage':
        filtered_ships = [ship for ship in filtered_ships if ship.crew_status == 'shortage']
    elif crew_filter == 'not_configured':
        filtered_ships = [ship for ship in filtered_ships if ship.crew_status == 'not_configured']
    # 'all' - без фильтрации
    
    # Сортировка судов
    ship_sort = request.GET.get('ship_sort', 'name')
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
    voyage_filter = request.GET.get('voyage_filter', 'current')
    now = timezone.now()
    current_voyages = filter_voyages_by_health(
        Voyage.objects.select_related('ship'),
        health_filter=voyage_filter,
        now=now,
    )

    # Сортировка рейсов
    voyage_sort = request.GET.get('voyage_sort', 'date')
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
        'voyage_filter': voyage_filter,
        'voyage_sort': voyage_sort,
    }
    return render(request, 'fleet/dashboard.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew', 'dispatcher', 'engineer'])
def ship_detail(request, ship_id):
    """Страница детальной информации о судне"""
    ship = get_object_or_404(Ship, id=ship_id)
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
            # Валидация
            if not name or not imo:
                messages.error(request, 'Название и номер ИМО обязательны')
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
                technical_condition=technical_condition
            )
            messages.success(request, f'Судно {ship.name} успешно создано.')
            return redirect('fleet:ship_detail', ship_id=ship.id)
        except Exception as e:
            messages.error(request, f'Ошибка при создании судна: {e}')
            return redirect('fleet:ship_create')
    context = {
        'page_title': 'Добавить судно',
        'location_choices': location_choices,
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
            ship.save()
            messages.success(request, f'Судно {ship.name} успешно обновлено.')
            return redirect('fleet:ship_detail', ship_id=ship.id)
        except Exception as e:
            messages.error(request, f'Ошибка при обновлении судна: {e}')
            return redirect('fleet:ship_edit', ship_id=ship.id)
    context = {
        'ship': ship,
        'page_title': f'Редактировать {ship.name}',
        'location_choices': location_choices,
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
            start_date = parse_datetime(start_date_str)
            if not start_date:
                start_date = timezone.now()
            end_date = parse_datetime(end_date_str) if end_date_str else None
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
        'page_title': 'Создать рейс'
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
            start_date = parse_datetime(start_date_str)
            if not start_date:
                start_date = timezone.now()
            end_date = parse_datetime(end_date_str) if end_date_str else None
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
        'page_title': 'Редактировать рейс'
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
            end_date = parse_datetime(end_date_raw) if end_date_raw else timezone.now()

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
    context = {'voyage': voyage, 'page_title': 'Завершить рейс'}
    return render(request, 'fleet/voyage_complete.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew'])
def crew_list(request):
    """Список всего экипажа с фильтрацией по статусу контракта"""
    from django.utils import timezone
    from django.db.models import Q
    
    crew = CrewMember.objects.all().select_related('user', 'assigned_ship')
    
    # Получаем параметр фильтрации
    contract_filter = request.GET.get('contract_status', 'all')
    today = timezone.now().date()
    
    # Применяем фильтрацию по статусу контракта
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
    # 'all' - без фильтрации
    
    # Сортировка по умолчанию: сначала истёкшие, потом скоро истекающие, потом остальные
    crew = crew.order_by(
        'contract_end_date',  # null first для no_contract
        'rank',
        'user__last_name'
    )
    
    context = {
        'crew': crew,
        'page_title': 'Экипаж',
        'today': today,
        'contract_filter': contract_filter,
    }
    return render(request, 'fleet/crew_list.html', context)
@login_required
@role_required(['director', 'fleet_manager', 'crew', 'engineer', 'dispatcher'])
def maintenance_list(request):
    """Список всех ремонтов"""
    
    # Фильтрация по проблемности
    maintenance_filter = request.GET.get('maintenance_filter', 'all')
    today = timezone.now().date()
    maintenances = filter_maintenances_by_health(
        Maintenance.objects.all(),
        health_filter=maintenance_filter,
        today=today,
    )
    
    if False and maintenance_filter == 'overdue':
        # Просроченные (planned, но дата прошла)
        maintenances = maintenances.filter(status='planned', start_date__lt=today)
    elif False and maintenance_filter == 'too_long':
        # Слишком долгие (in_progress слишком долго)
        maintenances = maintenances.filter(
            status='in_progress',
            start_date__lte=today - timezone.timedelta(days=MAINTENANCE_TOO_LONG_DAYS)
        )
    elif False and maintenance_filter == 'normal':
        # Нормальные (не просроченные и не слишком долгие)
        maintenances = maintenances.exclude(
            status='planned', start_date__lt=today
        ).exclude(
            status='in_progress',
            start_date__lte=today - timezone.timedelta(days=MAINTENANCE_TOO_LONG_DAYS)
        )
    # 'all' - без фильтрации
    
    # Сортировка
    maintenance_sort = request.GET.get('maintenance_sort', 'date')
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
            start_date = parse_date(start_date_str) if start_date_str else timezone.now().date()
            end_date = parse_date(end_date_str) if end_date_str else None
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
            messages.success(request, f'Запись о ремонте для судна {ship.name} успешно создана.')
            return redirect('fleet:maintenance_list')
        except Exception as e:
            messages.error(request, f'Ошибка при создании записи: {e}')
            return redirect('fleet:maintenance_create')
    context = {
        'ship': ship,
        'ships': ships,
        'contractors': contractors,
        'page_title': 'Добавить ТО/ремонт',
        'maintenance_type_choices': Maintenance.MAINTENANCE_TYPE_CHOICES,
        'status_choices': Maintenance.STATUS_CHOICES,
    }
    return render(request, 'fleet/maintenance_form.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew', 'engineer', 'dispatcher'])
def maintenance_detail(request, maintenance_id):
    """Детальная информация о ремонте"""
    maintenance = get_object_or_404(Maintenance, id=maintenance_id)
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
    maintenance = get_object_or_404(Maintenance, id=maintenance_id)
    ships = Ship.objects.all().order_by('name')
    contractors = Contractor.objects.all().order_by('name')
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
            start_date = parse_date(start_date_str) if start_date_str else maintenance.start_date
            end_date = parse_date(end_date_str) if end_date_str else None
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
            messages.success(request, f'Запись о ремонте для судна {ship.name} успешно обновлена.')
            return redirect('fleet:maintenance_list')
        except Exception as e:
            messages.error(request, f'Ошибка при обновлении записи: {e}')
            return redirect('fleet:maintenance_edit', maintenance_id=maintenance.id)
    context = {
        'maintenance': maintenance,
        'ships': ships,
        'contractors': contractors,
        'page_title': 'Редактировать ТО/ремонт',
        'maintenance_type_choices': Maintenance.MAINTENANCE_TYPE_CHOICES,
        'status_choices': Maintenance.STATUS_CHOICES,
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
    ships = Ship.objects.all()
    if request.method == 'POST':
        try:
            username = request.POST.get('username')
            password = request.POST.get('password')
            email = request.POST.get('email')
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            patronymic = request.POST.get('patronymic', '')
            rank = request.POST.get('rank', 'seaman')
            assigned_ship_id = request.POST.get('assigned_ship')
            assigned_ship = Ship.objects.get(id=assigned_ship_id) if assigned_ship_id else None
            # Создаем пользователя
            user = User.objects.create_user(
                username=username,
                password=password,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
            # Создаем роль 'crew'
            UserRole.objects.create(user=user, role='crew')
            # Создаем CrewMember
            crew = CrewMember.objects.create(
                user=user,
                rank=rank,
                assigned_ship=assigned_ship,
                notes=f'Отчество: {patronymic}' if patronymic else ''
            )
            messages.success(request, f'Член экипажа {user.get_full_name()} успешно создан.')
            return redirect('fleet:crew_list')
        except Exception as e:
            messages.error(request, f'Ошибка при создании члена экипажа: {e}')
            return redirect('fleet:crew_create')
    context = {
        'ships': ships,
        'page_title': 'Добавить члена экипажа',
        'rank_choices': CrewMember.RANK_CHOICES,
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

    start_date = parse_date(start_date_str) if start_date_str else default_start
    end_date = parse_date(end_date_str) if end_date_str else default_end

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
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)
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
