#!/usr/bin/env python
import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'erp.settings')
django.setup()

from fleet.models import Ship

def test_engine_hours():
    # Get or create a test ship
    ship = Ship.objects.first()
    if not ship:
        print("No ships found, creating one...")
        ship = Ship.objects.create(
            name='Test Ship',
            imo_number='TEST123',
            type='fishing',
            status='in_port'
        )
    
    print(f"Testing ship: {ship.name} (ID: {ship.id})")
    
    # 1. Value = None
    ship.engine_hours = None
    ship.save()
    ship.refresh_from_db()
    print(f"1. engine_hours = None -> {ship.engine_hours}")
    
    # 2. Value = 0
    ship.engine_hours = 0
    ship.save()
    ship.refresh_from_db()
    print(f"2. engine_hours = 0 -> {ship.engine_hours}")
    
    # 3. Positive number
    ship.engine_hours = 1500
    ship.save()
    ship.refresh_from_db()
    print(f"3. engine_hours = 1500 -> {ship.engine_hours}")
    
    # 4. Edge case: empty string (should be handled by validation)
    # We'll try to assign empty string directly to model field (bypass validation)
    # This might cause error, but we'll see.
    try:
        ship.engine_hours = ''
        ship.save()
        ship.refresh_from_db()
        print(f"4. engine_hours = '' -> {ship.engine_hours}")
    except Exception as e:
        print(f"4. engine_hours = '' -> Error: {e}")
    
    # 5. Check template rendering via test client
    from django.test import Client
    from django.contrib.auth.models import User
    from accounts.models import UserRole
    
    # Create a test user with appropriate role
    user, created = User.objects.get_or_create(username='testuser')
    if created:
        user.set_password('testpass')
        user.save()
        UserRole.objects.get_or_create(user=user, role='director')
    
    client = Client()
    client.force_login(user)
    response = client.get(f'/fleet/ships/{ship.id}/')
    if response.status_code == 200:
        print("5. Ship detail page loads successfully")
        # Check if engine_hours appears in response
        content = response.content.decode('utf-8')
        if 'Наработка двигателя' in content:
            print("   - 'Наработка двигателя' found in page")
        else:
            print("   - WARNING: 'Наработка двигателя' not found")
    else:
        print(f"5. Ship detail page failed: {response.status_code}")
    
    # 6. Check dashboard and lists
    response = client.get('/fleet/')
    if response.status_code == 200:
        print("6. Dashboard loads successfully")
    else:
        print(f"6. Dashboard failed: {response.status_code}")
    
    # Clean up (optional)
    # ship.delete()

if __name__ == '__main__':
    test_engine_hours()