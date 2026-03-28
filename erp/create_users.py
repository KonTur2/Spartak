# create_test_users.py
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
django.setup()

from django.contrib.auth.models import User
from accounts.models import UserRole

# Словарь с тестовыми пользователями
test_users = [
    {'username': 'director', 'password': 'password123', 'email': 'director@spartak.ru', 'role': 'director'},
    {'username': 'accountant', 'password': 'password123', 'email': 'accountant@spartak.ru', 'role': 'accountant'},
    {'username': 'logistician', 'password': 'password123', 'email': 'logistician@spartak.ru', 'role': 'logistician'},
    {'username': 'crew', 'password': 'password123', 'email': 'crew@spartak.ru', 'role': 'crew'},
    {'username': 'fleet_manager', 'password': 'password123', 'email': 'fleet_manager@spartak.ru', 'role': 'fleet_manager'},
    {'username': 'dispatcher', 'password': 'dispatcher123', 'email': 'dispatcher@spartak.ru', 'role': 'dispatcher'},
    {'username': 'engineer', 'password': 'engineer123', 'email': 'engineer@spartak.ru', 'role': 'engineer'},
]

def create_users():
    for user_data in test_users:
        # Проверяем, существует ли пользователь
        if not User.objects.filter(username=user_data['username']).exists():
            # Создаем пользователя
            user = User.objects.create_user(
                username=user_data['username'],
                password=user_data['password'],
                email=user_data['email']
            )
            
            # Устанавливаем роль
            user_role, created = UserRole.objects.get_or_create(user=user)
            user_role.role = user_data['role']
            user_role.save()
            
            print(f"Создан пользователь: {user_data['username']} с ролью {user_data['role']}")
        else:
            print(f"Пользователь {user_data['username']} уже существует")

if __name__ == '__main__':
    create_users()
    print("Тестовые пользователи созданы!")