from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Sum, Q, Case, When, IntegerField
from django.utils import timezone

from accounts.decorators import role_required
from .models import Ship, CrewMember, Voyage, Maintenance, Contractor


@login_required
@role_required(['director', 'fleet_manager', 'crew'])
def fleet_dashboard(request):
    """Главная страница модуля управления флотом"""
    ships = Ship.objects.all().order_by('status', 'name')
    ships_at_sea = Ship.objects.filter(status='at_sea').count()
    ships_in_port = Ship.objects.filter(status='in_port').count()
    ships_under_repair = Ship.objects.filter(status='under_repair').count()

    # Текущие рейсы (не завершённые)
    current_voyages = Voyage.objects.filter(is_completed=False).select_related('ship')

    # Последние ремонты
    recent_maintenances = Maintenance.objects.all().order_by('-start_date')[:5]

    context = {
        'ships': ships,
        'ships_at_sea': ships_at_sea,
        'ships_in_port': ships_in_port,
        'ships_under_repair': ships_under_repair,
        'current_voyages': current_voyages,
        'recent_maintenances': recent_maintenances,
        'page_title': 'Управление флотом',
    }
    return render(request, 'fleet/dashboard.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew'])
def ship_detail(request, ship_id):
    """Страница детальной информации о судне"""
    ship = get_object_or_404(Ship, id=ship_id)
    crew = CrewMember.objects.filter(assigned_ship=ship).select_related('user')
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
@role_required(['director', 'fleet_manager'])
def voyage_create(request, ship_id=None):
    """Создание нового рейса"""
    ship = None
    if ship_id:
        ship = get_object_or_404(Ship, id=ship_id)
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
            # Преобразование дат
            from django.utils.dateparse import parse_datetime
            start_date = parse_datetime(start_date_str)
            if not start_date:
                start_date = timezone.now()
            end_date = parse_datetime(end_date_str) if end_date_str else None
            # Преобразуем наивные datetime в aware, если необходимо
            if start_date and timezone.is_naive(start_date):
                start_date = timezone.make_aware(start_date)
            if end_date and timezone.is_naive(end_date):
                end_date = timezone.make_aware(end_date)
            # Проверка пересечения рейсов (простая)
            overlapping = Voyage.objects.filter(
                ship=ship,
                is_completed=False,
                start_date__lt=end_date if end_date else timezone.now() + timezone.timedelta(days=365),
                end_date__gt=start_date,
            ).exists()
            if overlapping:
                messages.warning(request, 'Судно уже находится в рейсе в указанный период. Рейс не создан.')
                return redirect('fleet:voyage_create')
            # Создание рейса
            voyage = Voyage.objects.create(
                ship=ship,
                start_date=start_date,
                end_date=end_date,
                fishing_area=fishing_area,
                catch_plan=catch_plan,
                is_completed=False,
            )
            # Добавление экипажа с проверкой пересечения
            crew_ids = request.POST.getlist('crew')
            if crew_ids:
                crew = CrewMember.objects.filter(id__in=crew_ids)
                # Проверка пересечения для каждого члена экипажа
                overlapping_crew = []
                for c in crew:
                    overlapping_voyages = Voyage.objects.filter(
                        crew=c,
                        is_completed=False,
                        start_date__lt=end_date if end_date else timezone.now() + timezone.timedelta(days=365),
                        end_date__gt=start_date,
                    ).exclude(id=voyage.id if voyage else None)
                    if overlapping_voyages.exists():
                        overlapping_crew.append(c)
                if overlapping_crew:
                    names = ', '.join([c.user.get_full_name() for c in overlapping_crew])
                    messages.warning(
                        request,
                        f'Следующие члены экипажа уже заняты в других рейсах в указанный период: {names}. '
                        'Рейс не создан.'
                    )
                    return redirect('fleet:voyage_create')
                voyage.crew.set(crew)
            messages.success(request, f'Рейс для судна {ship.name} успешно создан.')
            return redirect('fleet:fleet_dashboard')
        except Exception as e:
            messages.error(request, f'Ошибка при создании рейса: {e}')
            return redirect('fleet:voyage_create')
    context = {
        'ship': ship,
        'ships': ships,
        'crew_members': crew_members,
        'selected_crew_ids': [],
        'page_title': 'Создать рейс'
    }
    return render(request, 'fleet/voyage_form.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
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
            # Преобразование дат
            from django.utils.dateparse import parse_datetime
            start_date = parse_datetime(start_date_str)
            if not start_date:
                start_date = timezone.now()
            end_date = parse_datetime(end_date_str) if end_date_str else None
            # Преобразуем наивные datetime в aware, если необходимо
            if start_date and timezone.is_naive(start_date):
                start_date = timezone.make_aware(start_date)
            if end_date and timezone.is_naive(end_date):
                end_date = timezone.make_aware(end_date)
            # Проверка пересечения рейсов (исключая текущий рейс)
            overlapping = Voyage.objects.filter(
                ship=ship,
                is_completed=False,
                start_date__lt=end_date if end_date else timezone.now() + timezone.timedelta(days=365),
                end_date__gt=start_date,
            ).exclude(id=voyage.id).exists()
            if overlapping:
                messages.warning(request, 'Судно уже находится в рейсе в указанный период. Изменения не сохранены.')
                return redirect('fleet:voyage_edit', voyage_id=voyage.id)
            # Обновление рейса
            voyage.ship = ship
            voyage.start_date = start_date
            voyage.end_date = end_date
            voyage.fishing_area = fishing_area
            voyage.catch_plan = catch_plan
            voyage.save()
            # Обновление экипажа с проверкой пересечения
            crew_ids = request.POST.getlist('crew')
            if crew_ids:
                crew = CrewMember.objects.filter(id__in=crew_ids)
                # Проверка пересечения для каждого члена экипажа (исключая текущий рейс)
                overlapping_crew = []
                for c in crew:
                    overlapping_voyages = Voyage.objects.filter(
                        crew=c,
                        is_completed=False,
                        start_date__lt=end_date if end_date else timezone.now() + timezone.timedelta(days=365),
                        end_date__gt=start_date,
                    ).exclude(id=voyage.id)
                    if overlapping_voyages.exists():
                        overlapping_crew.append(c)
                if overlapping_crew:
                    names = ', '.join([c.user.get_full_name() for c in overlapping_crew])
                    messages.warning(
                        request,
                        f'Следующие члены экипажа уже заняты в других рейсах в указанный период: {names}. '
                        'Изменения не сохранены.'
                    )
                    return redirect('fleet:voyage_edit', voyage_id=voyage.id)
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
@role_required(['director', 'fleet_manager'])
def voyage_complete(request, voyage_id):
    """Завершение рейса с вводом фактической добычи"""
    voyage = get_object_or_404(Voyage, id=voyage_id)
    if request.method == 'POST':
        # В реальном проекте здесь будет форма
        voyage.is_completed = True
        voyage.end_date = timezone.now()
        voyage.save()
        messages.success(request, f'Рейс завершён. Факт добычи: {voyage.actual_catch} т')
        return redirect('fleet:ship_detail', ship_id=voyage.ship.id)
    context = {'voyage': voyage, 'page_title': 'Завершить рейс'}
    return render(request, 'fleet/voyage_complete.html', context)


@login_required
@role_required(['director', 'fleet_manager', 'crew'])
def crew_list(request):
    """Список всего экипажа"""
    crew = CrewMember.objects.all().select_related('user', 'assigned_ship').order_by('rank')
    context = {'crew': crew, 'page_title': 'Экипаж'}
    return render(request, 'fleet/crew_list.html', context)
@login_required
@role_required(['director', 'fleet_manager', 'crew'])
def maintenance_list(request):
    """Список всех ремонтов"""
    # Фильтрация и сортировка: сначала "В работе", потом "Запланировано", потом "Завершено"
    order = Case(
        When(status='in_progress', then=0),
        When(status='planned', then=1),
        When(status='completed', then=2),
        default=3,
        output_field=IntegerField(),
    )
    maintenances = Maintenance.objects.all().annotate(
        custom_order=order
    ).order_by('custom_order', '-start_date')
    
    context = {
        'maintenances': maintenances,
        'page_title': 'Судоремонт',
    }
    return render(request, 'fleet/maintenance_list.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
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
            # Преобразование дат
            from django.utils.dateparse import parse_date
            start_date = parse_date(start_date_str) if start_date_str else timezone.now().date()
            end_date = parse_date(end_date_str) if end_date_str else None
            # Определение контрагента
            contractor = None
            if contractor_id:
                try:
                    contractor = Contractor.objects.get(id=contractor_id)
                except Contractor.DoesNotExist:
                    pass
            # Создание записи
            maintenance = Maintenance.objects.create(
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
@role_required(['director', 'fleet_manager', 'crew'])
def maintenance_detail(request, maintenance_id):
    """Детальная информация о ремонте"""
    maintenance = get_object_or_404(Maintenance, id=maintenance_id)
    context = {
        'maintenance': maintenance,
        'page_title': f'Ремонт {maintenance.ship.name}',
    }
    return render(request, 'fleet/maintenance_detail.html', context)


@login_required
@role_required(['director', 'fleet_manager'])
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
            # Преобразование дат
            from django.utils.dateparse import parse_date
            start_date = parse_date(start_date_str) if start_date_str else maintenance.start_date
            end_date = parse_date(end_date_str) if end_date_str else None
            # Определение контрагента
            contractor = None
            if contractor_id:
                try:
                    contractor = Contractor.objects.get(id=contractor_id)
                except Contractor.DoesNotExist:
                    pass
            # Обновление записи
            maintenance.ship = ship
            maintenance.maintenance_type = maintenance_type
            maintenance.description = description
            maintenance.start_date = start_date
            maintenance.end_date = end_date
            maintenance.cost = cost
            maintenance.status = status
            maintenance.contractor = contractor
            maintenance.contractor_text = contractor_text
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
@role_required(['director', 'fleet_manager'])
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
