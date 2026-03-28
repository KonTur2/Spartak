# finance_app/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Sum, Q
from django.utils import timezone
from .models import Counterparty, Contract, Invoice, Payment, Employee, SalaryCalculation, ExpenseCategory, ExpenseItem
from .forms import (
    CounterpartyForm, ContractForm, InvoiceForm, 
    PaymentForm, EmployeeForm, SalaryCalculationForm
)


@login_required
def dashboard(request):
    """Дашборд финансового блока"""
    
    # Статистика
    total_counterparties = Counterparty.objects.count()
    total_contracts = Contract.objects.count()
    active_contracts = Contract.objects.filter(status='active').count()
    
    # Последние контрагенты
    recent_counterparties = Counterparty.objects.order_by('-created_at')[:5]
    
    # Последние договоры
    recent_contracts = Contract.objects.select_related('counterparty').order_by('-date')[:5]
    
    context = {
        'total_counterparties': total_counterparties,
        'total_contracts': total_contracts,
        'active_contracts': active_contracts,
        'recent_counterparties': recent_counterparties,
        'recent_contracts': recent_contracts,
    }
    return render(request, 'finance/dashboard.html', context)

@login_required
def counterparty_list(request):
    """Список контрагентов"""
    counterparties = Counterparty.objects.all()
    
    # Фильтрация по типу
    counterparty_type = request.GET.get('type')
    if counterparty_type:
        counterparties = counterparties.filter(counterparty_type=counterparty_type)
    
    # Поиск
    search = request.GET.get('search')
    if search:
        counterparties = counterparties.filter(name__icontains=search)
    
    # Статистика
    client_count = Counterparty.objects.filter(counterparty_type='client').count()
    supplier_count = Counterparty.objects.filter(counterparty_type='supplier').count()
    both_count = Counterparty.objects.filter(counterparty_type='both').count()
    active_count = Counterparty.objects.filter(is_active=True).count()
    
    context = {
        'counterparties': counterparties,
        'total': counterparties.count(),
        'filter_type': counterparty_type,
        'client_count': client_count,
        'supplier_count': supplier_count,
        'both_count': both_count,
        'active_count': active_count,
    }
    return render(request, 'finance/counterparty_list.html', context)

@login_required
def counterparty_detail(request, pk):
    """Детальная карточка контрагента"""
    counterparty = get_object_or_404(Counterparty, pk=pk)
    contracts = counterparty.contracts.all()
    
    context = {
        'counterparty': counterparty,
        'contracts': contracts,
    }
    return render(request, 'finance/counterparty_detail.html', context)

@login_required
def counterparty_create(request):
    """Создание нового контрагента"""
    if request.method == 'POST':
        form = CounterpartyForm(request.POST)
        if form.is_valid():
            counterparty = form.save(commit=False)
            counterparty.created_by = request.user
            counterparty.save()
            messages.success(request, f'Контрагент {counterparty.name} успешно создан')
            return redirect('finance:counterparty_detail', pk=counterparty.pk)
    else:
        form = CounterpartyForm()
    
    context = {
        'form': form,
        'title': 'Новый контрагент',
    }
    return render(request, 'finance/counterparty_form.html', context)

@login_required
def counterparty_edit(request, pk):
    """Редактирование контрагента"""
    counterparty = get_object_or_404(Counterparty, pk=pk)
    
    if request.method == 'POST':
        form = CounterpartyForm(request.POST, instance=counterparty)
        if form.is_valid():
            form.save()
            messages.success(request, f'Контрагент {counterparty.name} обновлен')
            return redirect('finance:counterparty_detail', pk=counterparty.pk)
    else:
        form = CounterpartyForm(instance=counterparty)
    
    context = {
        'form': form,
        'title': f'Редактирование: {counterparty.name}',
    }
    return render(request, 'finance/counterparty_form.html', context)

@login_required
def contract_list(request):
    """Список договоров"""
    contracts = Contract.objects.select_related('counterparty').all()
    
    # Фильтрация по статусу
    status = request.GET.get('status')
    if status:
        contracts = contracts.filter(status=status)
    
    # Фильтрация по типу
    contract_type = request.GET.get('type')
    if contract_type:
        contracts = contracts.filter(contract_type=contract_type)
    
    # Поиск по номеру или контрагенту
    search = request.GET.get('search')
    if search:
        contracts = contracts.filter(
            Q(number__icontains=search) |
            Q(counterparty__name__icontains=search)
        )
    
    # Статистика
    active_count = Contract.objects.filter(status='active').count()
    expired_count = Contract.objects.filter(status='expired').count()
    total_amount = contracts.aggregate(Sum('amount'))['amount__sum'] or 0
    
    context = {
        'contracts': contracts,
        'total': contracts.count(),
        'active_count': active_count,
        'expired_count': expired_count,
        'total_amount': total_amount,
    }
    return render(request, 'finance/contract_list.html', context)

@login_required
def contract_create(request):
    """Создание нового договора"""
    if request.method == 'POST':
        form = ContractForm(request.POST, request.FILES)
        if form.is_valid():
            contract = form.save(commit=False)
            contract.created_by = request.user
            contract.save()
            messages.success(request, f'Договор {contract.number} успешно создан')
            return redirect('finance:contract_list')
    else:
        form = ContractForm()
    
    context = {
        'form': form,
        'title': 'Новый договор',
    }
    return render(request, 'finance/contract_form.html', context)

@login_required
def payment_list(request, payment_type=None):
    """Список платежей с фильтрацией по типу"""
    payments = Payment.objects.select_related('counterparty', 'invoice').all()
    
    # Фильтрация по типу из URL
    if payment_type:
        payments = payments.filter(payment_type=payment_type)
    else:
        # Фильтрация по GET параметру
        filter_type = request.GET.get('type')
        if filter_type:
            payments = payments.filter(payment_type=filter_type)
    
    # Подсчет сумм
    total_amount = payments.aggregate(Sum('amount'))['amount__sum'] or 0
    
    # Статистика по типам
    incoming_total = Payment.objects.filter(payment_type='incoming').aggregate(Sum('amount'))['amount__sum'] or 0
    outgoing_total = Payment.objects.filter(payment_type='outgoing').aggregate(Sum('amount'))['amount__sum'] or 0
    
    context = {
        'payments': payments,
        'total': payments.count(),
        'total_amount': total_amount,
        'incoming_total': incoming_total,
        'outgoing_total': outgoing_total,
        'current_type': payment_type or request.GET.get('type', 'all'),
    }
    return render(request, 'finance/payment_list.html', context)


@login_required
def invoice_list(request):
    """Список счетов"""
    invoices = Invoice.objects.select_related('counterparty', 'contract').all()
    
    context = {
        'invoices': invoices,
        'total_amount': invoices.aggregate(Sum('amount'))['amount__sum'] or 0,
        'total_unpaid': sum(i.remaining_amount for i in invoices if i.remaining_amount > 0),
        'today': timezone.now().date(),  # добавляем сегодняшнюю дату
    }
    return render(request, 'finance/invoice_list.html', context)

@login_required
def employee_list(request):
    """Список сотрудников"""
    employees = Employee.objects.all()
    
    context = {
        'employees': employees,
        'total_shore': employees.filter(employee_type='shore').count(),
        'total_crew': employees.filter(employee_type='crew').count(),
    }
    return render(request, 'finance/employee_list.html', context)


@login_required
def counterparty_detail(request, pk):
    """Детальная карточка контрагента"""
    counterparty = get_object_or_404(Counterparty, pk=pk)
    contracts = counterparty.contracts.all()
    invoices = Invoice.objects.filter(counterparty=counterparty)
    payments = Payment.objects.filter(counterparty=counterparty)
    
    context = {
        'counterparty': counterparty,
        'contracts': contracts,
        'invoices': invoices,
        'payments': payments,
    }
    return render(request, 'finance/counterparty_detail.html', context)