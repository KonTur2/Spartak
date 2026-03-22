import django
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
django.setup()

from fleet.models import Maintenance, Contractor

print('Deleting all Maintenance records...')
count, _ = Maintenance.objects.all().delete()
print(f'Deleted {count} records.')

print('Creating default contractor...')
contractor, created = Contractor.objects.get_or_create(
    name='Судоремонтный завод №1',
    defaults={
        'contact_person': 'Иванов Иван',
        'phone': '+79123456789',
        'email': 'repair@example.com',
        'specialization': 'Общий ремонт',
        'notes': 'Основной подрядчик',
    }
)
print(f'Contractor created: {created}, id: {contractor.id}')