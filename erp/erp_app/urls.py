# erp_app/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Дашборд (только для директора)
    path('dashboard/', views.dashboard, name='dashboard'),
    
    # Добыча и переработка
    path('fishing/', views.fishing_view, name='fishing'),
    path('processing/', views.processing_view, name='processing'),
    
    # Сбыт и логистика
    path('sales/', views.sales_view, name='sales'),
    path('logistics/', views.logistics_view, name='logistics'),
    
    # Управление
    path('finance/', views.finance_view, name='finance'),
    path('documents/', views.documents_view, name='documents'),
    
    # Флот и судоремонт
    path('fleet/', views.fleet_view, name='fleet'),
    path('shiprepair/', views.shiprepair_view, name='shiprepair'),
]