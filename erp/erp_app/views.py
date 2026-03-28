# erp_app/views.py
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from datetime import datetime, timedelta
import random

def check_dashboard_access(user):
    """Проверяет, имеет ли пользователь доступ к дашборду"""
    if hasattr(user, 'role'):
        return user.role.role == 'director'
    return False

@login_required
def dashboard(request):
    # Проверяем, имеет ли пользователь доступ к дашборду
    if not check_dashboard_access(request.user):
        messages.warning(request, 'У вас нет доступа к дашборду. Вы будете перенаправлены в ваш раздел.')
        # Перенаправляем на соответствующий раздел
        from accounts.views import get_role_based_redirect
        return redirect(get_role_based_redirect(request.user))
    
    # Генерируем тестовые данные для дашборда
    context = {
        'user_role_display': request.user.role.get_role_display() if hasattr(request.user, 'role') else 'Пользователь',
        'role_greeting': 'Директор',
        
        # Статистика
        'total_products': random.randint(150, 300),
        'total_customers': random.randint(80, 150),
        'total_orders': random.randint(45, 90),
        'total_revenue': random.randint(1500000, 3000000),
        'active_vessels': random.randint(5, 12),
        
        # Данные для графиков
        'chart_labels': [(datetime.now() - timedelta(days=i)).strftime('%d.%m') for i in range(6, -1, -1)],
        'orders_data': [random.randint(5, 15) for _ in range(7)],
        'revenue_data': [random.randint(100000, 300000) for _ in range(7)],
        
        # Последние заказы
        'recent_orders': [
            {
                'id': f'ORD-{1000 + i}',
                'customer': f'Клиент {i}',
                'date': (datetime.now() - timedelta(days=i)).strftime('%d.%m.%Y'),
                'amount': random.randint(5000, 50000),
                'status': random.choice(['Выполнен', 'В обработке', 'Ожидает', 'Отменен'])
            }
            for i in range(1, 6)
        ],
        
        # Активные задачи
        'active_tasks': [
            {
                'title': f'Задача {i}',
                'priority': random.choice(['Высокий', 'Средний', 'Низкий']),
                'deadline': (datetime.now() + timedelta(days=random.randint(1, 5))).strftime('%d.%m.%Y'),
                'assigned_to': f'Сотрудник {random.randint(1, 5)}'
            }
            for i in range(1, 4)
        ],
    }
    
    return render(request, 'erp_app/dashboard.html', context)

@login_required
def finance_view(request):
    """Страница для бухгалтера"""
    if hasattr(request.user, 'role') and request.user.role.role not in ['director', 'accountant']:
        messages.error(request, 'У вас нет доступа к этому разделу')
        return redirect('dashboard')
    
    context = {
        'title': 'Управление финансами',
        'user_role': request.user.role.get_role_display() if hasattr(request.user, 'role') else '',
    }
    return render(request, 'erp_app/finance.html', context)

@login_required
def logistics_view(request):
    """Страница для логиста"""
    if hasattr(request.user, 'role') and request.user.role.role not in ['director', 'logistician']:
        messages.error(request, 'У вас нет доступа к этому разделу')
        return redirect('dashboard')
    
    context = {
        'title': 'Логистика',
        'user_role': request.user.role.get_role_display() if hasattr(request.user, 'role') else '',
    }
    return render(request, 'erp_app/logistics.html', context)

@login_required
def fleet_view(request):
    """Перенаправление на новый модуль управления флотом"""
    from fleet.views import fleet_dashboard
    return fleet_dashboard(request)


@login_required
def fishing_view(request):
    return render(request, 'erp_app/fishing.html', {'title': 'Добыча ВБР'})

@login_required
def processing_view(request):
    return render(request, 'erp_app/processing.html', {'title': 'Переработка'})

@login_required
def sales_view(request):
    return render(request, 'erp_app/sales.html', {'title': 'Сбыт'})

@login_required
def documents_view(request):
    return render(request, 'erp_app/documents.html', {'title': 'Документы'})

@login_required
def shiprepair_view(request):
    """Перенаправление на модуль судоремонта во флоте"""
    from fleet.views import maintenance_list
    return maintenance_list(request)