# finance_app/urls.py
from django.urls import path
from . import views

app_name = 'finance'

urlpatterns = [
    # Дашборд
    path('', views.dashboard, name='dashboard'),
    
    # Контрагенты
    path('counterparties/', views.counterparty_list, name='counterparty_list'),
    path('counterparties/<int:pk>/', views.counterparty_detail, name='counterparty_detail'),
    path('counterparties/create/', views.counterparty_create, name='counterparty_create'),
    path('counterparties/<int:pk>/edit/', views.counterparty_edit, name='counterparty_edit'),
    
    # Договоры
    path('contracts/', views.contract_list, name='contract_list'),
    path('contracts/create/', views.contract_create, name='contract_create'),
    
    # Платежи - исправленные маршруты
    path('payments/', views.payment_list, name='payment_list'),
    path('payments/incoming/', views.payment_list, {'payment_type': 'incoming'}, name='payment_incoming'),
    path('payments/outgoing/', views.payment_list, {'payment_type': 'outgoing'}, name='payment_outgoing'),
    
    # Счета
    path('invoices/', views.invoice_list, name='invoice_list'),
    
    # Сотрудники и зарплата
    path('employees/', views.employee_list, name='employee_list'),
]