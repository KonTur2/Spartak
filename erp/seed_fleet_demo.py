import os
from datetime import timedelta
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone

from accounts.models import UserRole
from fleet.models import Contractor, CrewMember, Maintenance, Ship, Voyage


def ensure_user(username, password, first_name, last_name, role_name='crew', email=None):
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            'first_name': first_name,
            'last_name': last_name,
            'email': email or f'{username}@spartak.ru',
        },
    )
    updated = False
    if user.first_name != first_name:
        user.first_name = first_name
        updated = True
    if user.last_name != last_name:
        user.last_name = last_name
        updated = True
    if email and user.email != email:
        user.email = email
        updated = True
    if created or not user.has_usable_password():
        user.set_password(password)
        updated = True
    if updated:
        user.save()

    user_role, _ = UserRole.objects.get_or_create(user=user)
    if user_role.role != role_name:
        user_role.role = role_name
        user_role.save()
    return user


def seed():
    now = timezone.now()
    today = now.date()

    Contractor.objects.all().delete()
    Maintenance.objects.all().delete()
    Voyage.objects.all().delete()
    CrewMember.objects.all().delete()
    Ship.objects.all().delete()

    contractors = {
        'yard_1': Contractor.objects.create(
            name='Судоремонтный завод №1',
            specialization='Капитальный ремонт',
            contact_person='Александр Панкратов',
        ),
        'yard_2': Contractor.objects.create(
            name='Судоремонтный завод №2',
            specialization='Текущий ремонт',
            contact_person='Игорь Лапшин',
        ),
        'dock': Contractor.objects.create(
            name='Доковый сервис',
            specialization='Докование и диагностика',
            contact_person='Марина Белова',
        ),
    }

    ships_data = [
        {'name': 'Спартак-1', 'imo_number': 'IMO1234567', 'type': 'fishing', 'current_location': 'Порт Владивосток', 'technical_condition': 'Исправно, готово к рейсу'},
        {'name': 'Восток', 'imo_number': 'IMO7654321', 'type': 'transport', 'current_location': 'Порт Находка', 'technical_condition': 'Исправно'},
        {'name': 'Морской Волк', 'imo_number': 'IMO1112223', 'type': 'fishing', 'current_location': 'Судоремонтный завод №1', 'technical_condition': 'Требуется ремонт двигателя'},
        {'name': 'Атлант', 'imo_number': 'IMO4445556', 'type': 'passenger', 'current_location': 'Порт Корсаков', 'technical_condition': 'Исправно'},
        {'name': 'Байкал', 'imo_number': 'IMO5556667', 'type': 'fishing', 'current_location': 'Берингово море', 'technical_condition': 'Исправно'},
        {'name': 'Тихий', 'imo_number': 'IMO6667778', 'type': 'transport', 'current_location': 'Порт Петропавловск-Камчатский', 'technical_condition': 'Плановое ТО через 20 дней'},
        {'name': 'Океан', 'imo_number': 'IMO7778889', 'type': 'passenger', 'current_location': 'Судоремонтный завод №2', 'technical_condition': 'Идет текущий ремонт'},
        {'name': 'Варяг', 'imo_number': 'IMO8889990', 'type': 'fishing', 'current_location': 'Японское море', 'technical_condition': 'Исправно'},
        {'name': 'Сахалин', 'imo_number': 'IMO9990001', 'type': 'transport', 'current_location': 'Порт Ванино', 'technical_condition': 'Исправно'},
        {'name': 'Амур', 'imo_number': 'IMO0001112', 'type': 'fishing', 'current_location': 'Порт Магадан', 'technical_condition': 'Есть замечания по навигации'},
    ]

    ships = {}
    for item in ships_data:
        ship = Ship.objects.create(
            name=item['name'],
            imo_number=item['imo_number'],
            type=item['type'],
            status='in_port',
            current_location=item['current_location'],
            technical_condition=item['technical_condition'],
        )
        ships[item['imo_number']] = ship

    Maintenance.objects.create(
        ship=ships['IMO1112223'],
        maintenance_type='current',
        description='Ремонт главного двигателя',
        start_date=today - timedelta(days=3),
        end_date=None,
        cost=Decimal('520000.00'),
        status='in_progress',
        contractor=contractors['yard_1'],
    )
    Maintenance.objects.create(
        ship=ships['IMO7778889'],
        maintenance_type='planned',
        description='Корпусные работы и проверка систем',
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=10),
        cost=Decimal('340000.00'),
        status='in_progress',
        contractor=contractors['yard_2'],
    )
    Maintenance.objects.create(
        ship=ships['IMO6667778'],
        maintenance_type='planned',
        description='Плановое ТО перед сезоном',
        start_date=today - timedelta(days=25),
        end_date=today - timedelta(days=20),
        cost=Decimal('180000.00'),
        status='completed',
        contractor=contractors['dock'],
    )
    Maintenance.objects.create(
        ship=ships['IMO9990001'],
        maintenance_type='current',
        description='Замена навигационного оборудования',
        start_date=today - timedelta(days=40),
        end_date=today - timedelta(days=35),
        cost=Decimal('210000.00'),
        status='completed',
        contractor=contractors['dock'],
    )

    Voyage.objects.create(
        ship=ships['IMO5556667'],
        start_date=now - timedelta(days=4),
        end_date=None,
        fishing_area='bering',
        catch_plan=210.0,
        actual_catch=None,
        description='Активный промысловый рейс',
        is_completed=False,
    )
    Voyage.objects.create(
        ship=ships['IMO8889990'],
        start_date=now - timedelta(days=2),
        end_date=now + timedelta(days=6),
        fishing_area='japan',
        catch_plan=160.0,
        actual_catch=None,
        description='Активный промысловый рейс',
        is_completed=False,
    )
    Voyage.objects.create(
        ship=ships['IMO1234567'],
        start_date=now - timedelta(days=18),
        end_date=now - timedelta(days=9),
        fishing_area='okhotsk',
        catch_plan=180.0,
        actual_catch=174.5,
        description='Завершенный рейс',
        is_completed=True,
    )
    Voyage.objects.create(
        ship=ships['IMO7654321'],
        start_date=now - timedelta(days=30),
        end_date=now - timedelta(days=24),
        fishing_area='pacific',
        catch_plan=90.0,
        actual_catch=88.0,
        description='Завершенный рейс',
        is_completed=True,
    )
    Voyage.objects.create(
        ship=ships['IMO4445556'],
        start_date=now - timedelta(days=14),
        end_date=now - timedelta(days=10),
        fishing_area='other',
        catch_plan=75.0,
        actual_catch=70.0,
        description='Завершенный рейс',
        is_completed=True,
    )

    crew_templates = {
        'IMO1234567': [('captain', 'cap_sp1', 'Илья', 'Соколов', 180), ('first_mate', 'mate_sp1', 'Максим', 'Орлов', 120), ('engineer', 'eng_sp1', 'Денис', 'Лукин', 200), ('seaman', 'sea_sp1_1', 'Павел', 'Белов', 140), ('cook', 'cook_sp1', 'Олег', 'Чернов', 160)],
        'IMO7654321': [('captain', 'cap_vst', 'Алексей', 'Петров', 200), ('first_mate', 'mate_vst', 'Игорь', 'Смирнов', 150), ('engineer', 'eng_vst', 'Роман', 'Егоров', 210), ('seaman', 'sea_vst_1', 'Николай', 'Зуев', 100)],
        'IMO1112223': [('captain', 'cap_wolf', 'Виктор', 'Комаров', 90), ('engineer', 'eng_wolf', 'Артем', 'Титов', 60), ('seaman', 'sea_wolf_1', 'Кирилл', 'Нестеров', 60)],
        'IMO4445556': [('captain', 'cap_atl', 'Сергей', 'Громов', 240), ('first_mate', 'mate_atl', 'Дмитрий', 'Серов', 220), ('engineer', 'eng_atl', 'Петр', 'Воронов', 220), ('cook', 'cook_atl', 'Марина', 'Лебедева', 300)],
        'IMO5556667': [('captain', 'cap_baikal', 'Евгений', 'Федоров', 110), ('first_mate', 'mate_baikal', 'Иван', 'Козлов', 110), ('engineer', 'eng_baikal', 'Антон', 'Тарасов', 110), ('seaman', 'sea_baikal_1', 'Глеб', 'Макаров', 95), ('seaman', 'sea_baikal_2', 'Юрий', 'Жуков', 95)],
        'IMO6667778': [('captain', 'cap_tih', 'Леонид', 'Буров', 365), ('first_mate', 'mate_tih', 'Степан', 'Фомин', 365), ('engineer', 'eng_tih', 'Константин', 'Белов', 365), ('cook', 'cook_tih', 'Анна', 'Морозова', 365)],
        'IMO7778889': [('captain', 'cap_oke', 'Борис', 'Демин', 45), ('first_mate', 'mate_oke', 'Георгий', 'Касаткин', 45), ('engineer', 'eng_oke', 'Андрей', 'Седов', 45)],
        'IMO8889990': [('captain', 'cap_var', 'Руслан', 'Литвинов', 150), ('first_mate', 'mate_var', 'Федор', 'Васильев', 150), ('engineer', 'eng_var', 'Тимур', 'Игнатьев', 150), ('seaman', 'sea_var_1', 'Семен', 'Грачев', 150), ('cook', 'cook_var', 'Елена', 'Давыдова', 150)],
        'IMO9990001': [('captain', 'cap_sah', 'Анатолий', 'Крылов', 200), ('first_mate', 'mate_sah', 'Матвей', 'Попов', 200), ('engineer', 'eng_sah', 'Владимир', 'Корнеев', 200), ('seaman', 'sea_sah_1', 'Егор', 'Шестаков', 200)],
        'IMO0001112': [('captain', 'cap_amur', 'Олег', 'Никитин', -5), ('first_mate', 'mate_amur', 'Ярослав', 'Савин', 70), ('engineer', 'eng_amur', 'Даниил', 'Семенов', 70), ('seaman', 'sea_amur_1', 'Ильдар', 'Крылов', 70)],
    }

    for imo_number, members in crew_templates.items():
        ship = ships[imo_number]
        for rank, username, first_name, last_name, days_left in members:
            user = ensure_user(username, 'password123', first_name, last_name)
            CrewMember.objects.create(
                user=user,
                rank=rank,
                assigned_ship=ship,
                contract_end_date=today + timedelta(days=days_left),
                notes='Тестовый состав экипажа',
            )

    print(f'Судов: {Ship.objects.count()}')
    print(f'Экипаж: {CrewMember.objects.count()}')
    print(f'Рейсов: {Voyage.objects.count()}')
    print(f'Ремонтов: {Maintenance.objects.count()}')


if __name__ == '__main__':
    seed()
