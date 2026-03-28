# finance_app/admin.py
from django.contrib import admin
from .models import *

@admin.register(Counterparty)
class CounterpartyAdmin(admin.ModelAdmin):
    list_display = ['name', 'inn', 'counterparty_type', 'is_active']
    list_filter = ['counterparty_type', 'is_active']
    search_fields = ['name', 'inn']

@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ['number', 'date', 'counterparty', 'contract_type', 'status', 'amount']
    list_filter = ['status', 'contract_type']
    search_fields = ['number', 'counterparty__name']

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['number', 'date', 'due_date', 'counterparty', 'amount', 'paid_amount', 'status']
    list_filter = ['status', 'invoice_type']
    search_fields = ['number', 'counterparty__name']

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['number', 'date', 'payment_type', 'counterparty', 'amount', 'payment_method']
    list_filter = ['payment_type', 'payment_method']
    search_fields = ['number', 'counterparty__name']

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'position', 'employee_type', 'is_active']
    list_filter = ['employee_type', 'is_active']
    search_fields = ['full_name']

@admin.register(SalaryCalculation)
class SalaryCalculationAdmin(admin.ModelAdmin):
    list_display = ['employee', 'period_start', 'period_end', 'total_amount', 'status']
    list_filter = ['status']
    search_fields = ['employee__full_name']