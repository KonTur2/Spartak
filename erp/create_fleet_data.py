import os
import django
import random
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
django.setup()

from django.utils import timezone
from django.contrib.auth.models import User
from fleet.models import Ship, CrewMember, Voyage, Maintenance
from accounts.models import UserRole


def create_test_data():
    # Создаём суда (10 штук)
    ships_data = [
        {'name': 'Спартак-1', 'imo_number': 'IMO1234567', 'type': 'fishing', 'status': 'at_sea', 'current_location': 'Охотское море'},
        {'name': 'Восток', 'imo_number': 'IMO7654321', 'type': 'transport', 'status': 'in_port', 'current_location': 'Порт Владивосток'},
        {'name': 'Морской волк', 'imo_number': 'IMO1112223', 'type': 'fishing', 'status': 'under_repair', 'current_location': 'Судоремонтный завод'},
        {'name': 'Атлант', 'imo_number': 'IMO4445556', 'type': 'passenger', 'status': 'in_port', 'current_location': 'Порт Находка'},
        {'name': 'Байкал', 'imo_number': 'IMO5556667', 'type': 'fishing', 'status': 'at_sea', 'current_location': 'Берингово море'},
        {'name': 'Тихий', 'imo_number': 'IMO6667778', 'type': 'transport', 'status': 'in_port', 'current_location': 'Порт Петропавловск-Камчатский'},
        {'name': 'Океан', 'imo_number': 'IMO7778889', 'type': 'passenger', 'status': 'under_repair', 'current_location': 'Судоремонтный завод №2'},
        {'name': 'Варяг', 'imo_number': 'IMO8889990', 'type': 'fishing', 'status': 'at_sea', 'current_location': 'Японское море'},
        {'name': 'Сахалин', 'imo_number': 'IMO9990001', 'type': 'transport', 'status': 'in_port', 'current_location': 'Порт Корсаков'},
        {'name': 'Амур', 'imo_number': 'IMO0001112', 'type': 'fishing', 'status': 'in_port', 'current_location': 'Порт Ванино'},
    ]
    ships = []
    for data in ships_data:
        ship, created = Ship.objects.get_or_create(
            imo_number=data['imo_number'],
            defaults={
                'name': data['name'],
                'type': data['type'],
                'status': data['status'],
                'current_location': data['current_location'],
                'technical_condition': 'Отличное' if data['status'] != 'under_repair' else 'Требуется ремонт двигателя'
            }
        )
        ships.append(ship)
        print(f"Судно {ship.name} создано/найдено")

    # Создаём дополнительных пользователей для экипажа (10 человек с ФИО)
    crew_users_data = [
        {'username': 'ivanov_ii', 'password': 'password123', 'email': 'ivanov@spartak.ru', 'first_name': 'Иван', 'last_name': 'Иванов', 'patronymic': 'Иванович', 'role': 'crew'},
        {'username': 'petrov_ap', 'password': 'password123', 'email': 'petrov@spartak.ru', 'first_name': 'Алексей', 'last_name': 'Петров', 'patronymic': 'Петрович', 'role': 'crew'},
        {'username': 'sidorova_ek', 'password': 'password123', 'email': 'sidorova@spartak.ru', 'first_name': 'Екатерина', 'last_name': 'Сидорова', 'patronymic': 'Сергеевна', 'role': 'crew'},
        {'username': 'smirnov_vv', 'password': 'password123', 'email': 'smirnov@spartak.ru', 'first_name': 'Владимир', 'last_name': 'Смирнов', 'patronymic': 'Владимирович', 'role': 'crew'},
        {'username': 'kuznetsov_aa', 'password': 'password123', 'email': 'kuznetsov@spartak.ru', 'first_name': 'Андрей', 'last_name': 'Кузнецов', 'patronymic': 'Александрович', 'role': 'crew'},
        {'username': 'popova_ol', 'password': 'password123', 'email': 'popova@spartak.ru', 'first_name': 'Ольга', 'last_name': 'Попова', 'patronymic': 'Леонидовна', 'role': 'crew'},
        {'username': 'volkov_ds', 'password': 'password123', 'email': 'volkov@spartak.ru', 'first_name': 'Дмитрий', 'last_name': 'Волков', 'patronymic': 'Сергеевич', 'role': 'crew'},
        {'username': 'kozlov_ia', 'password': 'password123', 'email': 'kozlov@spartak.ru', 'first_name': 'Игорь', 'last_name': 'Козлов', 'patronymic': 'Анатольевич', 'role': 'crew'},
        {'username': 'novikova_ma', 'password': 'password123', 'email': 'novikova@spartak.ru', 'first_name': 'Мария', 'last_name': 'Новикова', 'patronymic': 'Алексеевна', 'role': 'crew'},
        {'username': 'morozov_pv', 'password': 'password123', 'email': 'morozov@spartak.ru', 'first_name': 'Павел', 'last_name': 'Морозов', 'patronymic': 'Викторович', 'role': 'crew'},
    ]

    # Дополнительные 30 членов экипажа
    first_names = ['Александр', 'Михаил', 'Сергей', 'Анна', 'Елена', 'Денис', 'Николай', 'Татьяна', 'Юрий', 'Олег',
                   'Виктор', 'Наталья', 'Артём', 'Григорий', 'Людмила', 'Василий', 'Марина', 'Константин', 'Светлана', 'Роман']
    last_names = ['Фёдоров', 'Медведев', 'Киселёв', 'Орлов', 'Лебедев', 'Зайцев', 'Егоров', 'Тихонов', 'Фомин', 'Давыдов',
                  'Жуков', 'Соловьёв', 'Виноградов', 'Богданов', 'Воробьёв', 'Филиппов', 'Максимов', 'Ковалёв', 'Ильин', 'Герасимов']
    patronymics = ['Александрович', 'Михайлович', 'Сергеевич', 'Андреевич', 'Дмитриевич', 'Владимирович', 'Игоревич',
                   'Анатольевич', 'Юрьевич', 'Викторович', 'Олегович', 'Геннадьевич', 'Павлович', 'Романович', 'Аркадьевич']

    ranks = ['captain', 'first_mate', 'engineer', 'seaman', 'cook', 'navigator', 'radio_operator', 'boatswain', 'doctor', 'steward']

    # Создаём 30 дополнительных пользователей
    for i in range(30):
        first = random.choice(first_names)
        last = random.choice(last_names)
        pat = random.choice(patronymics)
        username = f"{last.lower()}_{first[0].lower()}{i+11}"  # уникальный
        email = f"{username}@spartak.ru"
        crew_users_data.append({
            'username': username,
            'password': 'password123',
            'email': email,
            'first_name': first,
            'last_name': last,
            'patronymic': pat,
            'role': 'crew'
        })

    crew_users = []
    for user_data in crew_users_data:
        user, created = User.objects.get_or_create(
            username=user_data['username'],
            defaults={
                'email': user_data['email'],
                'first_name': user_data['first_name'],
                'last_name': user_data['last_name'],
            }
        )
        if created:
            user.set_password(user_data['password'])
            user.save()
            # Создаем роль
            UserRole.objects.get_or_create(user=user, defaults={'role': user_data['role']})
            print(f"Создан пользователь: {user_data['username']} ({user_data['first_name']} {user_data['last_name']})")
        else:
            print(f"Пользователь {user_data['username']} уже существует")
        crew_users.append(user)

    # Создаём экипаж (CrewMember) для каждого пользователя
    for i, user in enumerate(crew_users):
        rank = ranks[i % len(ranks)]
        assigned_ship = ships[i % len(ships)]
        contract_end = timezone.now().date() + timedelta(days=365)
        notes = f'Отчество: {crew_users_data[i].get("patronymic", "")}'
        crew, created = CrewMember.objects.get_or_create(
            user=user,
            defaults={
                'rank': rank,
                'assigned_ship': assigned_ship,
                'contract_end_date': contract_end,
                'notes': notes
            }
        )
        if created:
            print(f"Член экипажа {user.get_full_name()} создан, должность: {rank}")
        else:
            print(f"Член экипажа {user.get_full_name()} уже существует")

    # Также создаем экипаж для существующих пользователей (crew, fleet_manager, director)
    existing_users = User.objects.filter(username__in=['crew', 'fleet_manager', 'director'])
    for i, user in enumerate(existing_users):
        crew, created = CrewMember.objects.get_or_create(
            user=user,
            defaults={
                'rank': ranks[i % len(ranks)],
                'assigned_ship': ships[i % len(ships)],
                'contract_end_date': timezone.now().date() + timedelta(days=365),
                'notes': 'Существующий пользователь'
            }
        )
        if created:
            print(f"Член экипажа {user.username} создан")

    # Создаём рейсы для некоторых судов
    for ship in ships[:5]:
        voyage, created = Voyage.objects.get_or_create(
            ship=ship,
            start_date=timezone.now() - timedelta(days=random.randint(1, 30)),
            defaults={
                'fishing_area': random.choice(['okhotsk', 'bering', 'japan', 'pacific', 'other']),
                'catch_plan': round(random.uniform(50.0, 300.0), 1),
                'actual_catch': round(random.uniform(40.0, 280.0), 1) if ship.status == 'at_sea' else None,
                'is_completed': ship.status != 'at_sea'
            }
        )
        if created:
            print(f"Рейс для {ship.name} создан")

    # Создаём ТО/ремонты для некоторых судов
    for ship in ships[5:8]:
        maint, created = Maintenance.objects.get_or_create(
            ship=ship,
            date=timezone.now().date() - timedelta(days=random.randint(1, 60)),
            defaults={
                'maintenance_type': random.choice(['scheduled', 'emergency', 'upgrade', 'inspection']),
                'description': 'Плановый осмотр и замена фильтров',
                'cost': round(random.uniform(10000.0, 200000.0), 2),
                'contractor': random.choice(['Судоремонтный завод №1', 'Судоремонтный завод №2', 'Внутренний отдел'])
            }
        )
        if created:
            print(f"ТО для {ship.name} создано")

    print("Тестовые данные для модуля fleet успешно созданы!")
    print(f"Всего судов: {Ship.objects.count()}")
    print(f"Всего членов экипажа: {CrewMember.objects.count()}")


if __name__ == '__main__':
    create_test_data()