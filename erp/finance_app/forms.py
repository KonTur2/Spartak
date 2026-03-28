# finance_app/forms.py
from django import forms
from .models import Counterparty, Contract, Invoice, Payment, Employee, SalaryCalculation, ExpenseItem

class CounterpartyForm(forms.ModelForm):
    """Форма для создания/редактирования контрагента"""
    
    class Meta:
        model = Counterparty
        fields = [
            'name', 'short_name', 'inn', 'kpp', 'counterparty_type',
            'phone', 'email', 'address',
            'bank_name', 'bank_bik', 'bank_account', 'correspondent_account'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Полное название'}),
            'short_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Краткое название'}),
            'inn': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '10 или 12 цифр'}),
            'kpp': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '9 цифр'}),
            'counterparty_type': forms.Select(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+7 (999) 123-45-67'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'example@mail.ru'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'bank_name': forms.TextInput(attrs={'class': 'form-control'}),
            'bank_bik': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'БИК'}),
            'bank_account': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Расчетный счет'}),
            'correspondent_account': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Корр. счет'}),
        }
    
    def clean_inn(self):
        """Валидация ИНН"""
        inn = self.cleaned_data.get('inn')
        if inn and len(inn) not in [10, 12]:
            raise forms.ValidationError('ИНН должен содержать 10 или 12 цифр')
        return inn
    
    def clean_kpp(self):
        """Валидация КПП"""
        kpp = self.cleaned_data.get('kpp')
        if kpp and len(kpp) != 9:
            raise forms.ValidationError('КПП должен содержать 9 цифр')
        return kpp


class ContractForm(forms.ModelForm):
    """Форма для создания/редактирования договора"""
    
    class Meta:
        model = Contract
        fields = [
            'number', 'date', 'counterparty', 'contract_type', 'status',
            'start_date', 'end_date', 'amount', 'currency', 'description', 'file'
        ]
        widgets = {
            'number': forms.TextInput(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'counterparty': forms.Select(attrs={'class': 'form-control'}),
            'contract_type': forms.Select(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.TextInput(attrs={'class': 'form-control', 'value': 'RUB'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'file': forms.FileInput(attrs={'class': 'form-control'}),
        }
    
    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        
        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError('Дата окончания не может быть раньше даты начала')
        
        return cleaned_data


class InvoiceForm(forms.ModelForm):
    """Форма для создания счета"""
    
    class Meta:
        model = Invoice
        fields = ['number', 'date', 'due_date', 'invoice_type', 'contract', 
                 'counterparty', 'amount', 'description', 'file']
        widgets = {
            'number': forms.TextInput(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'invoice_type': forms.Select(attrs={'class': 'form-control'}),
            'contract': forms.Select(attrs={'class': 'form-control'}),
            'counterparty': forms.Select(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'file': forms.FileInput(attrs={'class': 'form-control'}),
        }


class PaymentForm(forms.ModelForm):
    """Форма для регистрации платежа"""
    
    class Meta:
        model = Payment
        fields = ['number', 'date', 'payment_type', 'invoice', 'counterparty',
                 'amount', 'payment_method', 'external_order_id', 
                 'external_supply_request_id', 'comment']
        widgets = {
            'number': forms.TextInput(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'payment_type': forms.Select(attrs={'class': 'form-control'}),
            'invoice': forms.Select(attrs={'class': 'form-control'}),
            'counterparty': forms.Select(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'payment_method': forms.Select(attrs={'class': 'form-control'}),
            'external_order_id': forms.TextInput(attrs={'class': 'form-control'}),
            'external_supply_request_id': forms.TextInput(attrs={'class': 'form-control'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class EmployeeForm(forms.ModelForm):
    """Форма для сотрудника"""
    
    class Meta:
        model = Employee
        fields = ['full_name', 'position', 'employee_type', 'ship_name',
                 'base_salary', 'hourly_rate', 'bank_name', 'bank_account',
                 'is_active', 'hire_date', 'fire_date']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'position': forms.TextInput(attrs={'class': 'form-control'}),
            'employee_type': forms.Select(attrs={'class': 'form-control'}),
            'ship_name': forms.TextInput(attrs={'class': 'form-control'}),
            'base_salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'bank_name': forms.TextInput(attrs={'class': 'form-control'}),
            'bank_account': forms.TextInput(attrs={'class': 'form-control'}),
            'hire_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'fire_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }


class SalaryCalculationForm(forms.ModelForm):
    """Форма для начисления зарплаты"""
    
    class Meta:
        model = SalaryCalculation
        fields = ['employee', 'period_start', 'period_end', 'days_worked',
                 'hours_worked', 'base_amount', 'bonus_amount', 'voyage_days',
                 'voyage_rate', 'voyage_amount', 'comment']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-control'}),
            'period_start': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'period_end': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'days_worked': forms.NumberInput(attrs={'class': 'form-control'}),
            'hours_worked': forms.NumberInput(attrs={'class': 'form-control'}),
            'base_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'bonus_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'voyage_days': forms.NumberInput(attrs={'class': 'form-control'}),
            'voyage_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'voyage_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'comment': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }