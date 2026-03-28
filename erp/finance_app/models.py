# finance_app/models.py
from django.db import models
from django.contrib.auth import get_user_model
from datetime import timezone

User = get_user_model()

class Counterparty(models.Model):
    """Контрагент (клиент или поставщик)"""
    
    COUNTERPARTY_TYPES = [
        ('client', 'Клиент'),
        ('supplier', 'Поставщик'),
        ('both', 'Клиент и поставщик'),
    ]
    
    # Основная информация
    name = models.CharField('Название', max_length=255)
    short_name = models.CharField('Краткое название', max_length=100, blank=True)
    inn = models.CharField('ИНН', max_length=12, unique=True)
    kpp = models.CharField('КПП', max_length=9, blank=True)
    
    # Тип и статус
    counterparty_type = models.CharField('Тип', max_length=20, choices=COUNTERPARTY_TYPES)
    is_active = models.BooleanField('Активный', default=True)
    
    # Контактная информация
    phone = models.CharField('Телефон', max_length=20, blank=True)
    email = models.EmailField('Email', blank=True)
    address = models.TextField('Адрес', blank=True)
    
    # Банковские реквизиты
    bank_name = models.CharField('Название банка', max_length=255, blank=True)
    bank_bik = models.CharField('БИК', max_length=9, blank=True)
    bank_account = models.CharField('Расчетный счет', max_length=20, blank=True)
    correspondent_account = models.CharField('Корр. счет', max_length=20, blank=True)
    
    # Метаданные
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='Кто создал')
    
    class Meta:
        verbose_name = 'Контрагент'
        verbose_name_plural = 'Контрагенты'
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def get_full_name(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.short_name:
            self.short_name = self.name[:100]
        super().save(*args, **kwargs)
        
        
class Contract(models.Model):
    """Договор с контрагентом"""
    
    CONTRACT_TYPES = [
        ('sale', 'Договор продажи'),
        ('purchase', 'Договор поставки'),
        ('service', 'Договор услуг'),
        ('other', 'Прочее'),
    ]
    
    CONTRACT_STATUSES = [
        ('draft', 'Черновик'),
        ('active', 'Действует'),
        ('expired', 'Истек'),
        ('terminated', 'Расторгнут'),
    ]
    
    # Основная информация
    number = models.CharField('Номер договора', max_length=50)
    date = models.DateField('Дата договора')
    counterparty = models.ForeignKey(Counterparty, on_delete=models.PROTECT, verbose_name='Контрагент', related_name='contracts')
    
    # Тип и статус
    contract_type = models.CharField('Тип договора', max_length=20, choices=CONTRACT_TYPES)
    status = models.CharField('Статус', max_length=20, choices=CONTRACT_STATUSES, default='draft')
    
    # Сроки
    start_date = models.DateField('Дата начала')
    end_date = models.DateField('Дата окончания', null=True, blank=True)
    
    # Финансовые условия
    amount = models.DecimalField('Сумма', max_digits=15, decimal_places=2, null=True, blank=True)
    currency = models.CharField('Валюта', max_length=3, default='RUB')
    
    # Описание и файлы
    description = models.TextField('Описание', blank=True)
    file = models.FileField('Файл договора', upload_to='contracts/', null=True, blank=True)
    
    # Метаданные
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='Кто создал')
    
    class Meta:
        verbose_name = 'Договор'
        verbose_name_plural = 'Договоры'
        ordering = ['-date']
        unique_together = ['number', 'counterparty']  # У одного контрагента номера договоров уникальны
    
    def __str__(self):
        return f"{self.number} от {self.date.strftime('%d.%m.%Y')}"
    
    def is_expired(self):
        """Проверка, истек ли договор"""
        if self.end_date and self.end_date < timezone.now().date():
            return True
        return False
    is_expired.boolean = True
    is_expired.short_description = 'Истек'
    

class Invoice(models.Model):
    """Счет (выставленный клиенту или полученный от поставщика)"""
    
    INVOICE_TYPES = [
        ('outgoing', 'Исходящий (клиенту)'),
        ('incoming', 'Входящий (от поставщика)'),
    ]
    
    INVOICE_STATUSES = [
        ('draft', 'Черновик'),
        ('sent', 'Отправлен'),
        ('paid', 'Оплачен'),
        ('overdue', 'Просрочен'),
        ('cancelled', 'Отменен'),
    ]
    
    # Основная информация
    number = models.CharField('Номер счета', max_length=50)
    date = models.DateField('Дата счета')
    due_date = models.DateField('Срок оплаты')
    
    # Связи
    invoice_type = models.CharField('Тип', max_length=20, choices=INVOICE_TYPES)
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, verbose_name='Договор', related_name='invoices')
    counterparty = models.ForeignKey(Counterparty, on_delete=models.PROTECT, verbose_name='Контрагент', related_name='invoices')
    
    # Финансовые данные
    amount = models.DecimalField('Сумма', max_digits=15, decimal_places=2)
    paid_amount = models.DecimalField('Оплачено', max_digits=15, decimal_places=2, default=0)
    status = models.CharField('Статус', max_length=20, choices=INVOICE_STATUSES, default='draft')
    
    # Дополнительно
    description = models.TextField('Описание', blank=True)
    file = models.FileField('Файл счета', upload_to='invoices/', null=True, blank=True)
    
    # Метаданные
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='Кто создал')
    
    class Meta:
        verbose_name = 'Счет'
        verbose_name_plural = 'Счета'
        ordering = ['-date']
    
    def __str__(self):
        return f"{self.number} от {self.date.strftime('%d.%m.%Y')}"
    
    @property
    def remaining_amount(self):
        """Остаток к оплате"""
        return self.amount - self.paid_amount
    
    @property
    def is_fully_paid(self):
        return self.paid_amount >= self.amount


class Payment(models.Model):
    """Платеж (поступление или расход)"""
    
    PAYMENT_TYPES = [
        ('incoming', 'Поступление (от клиента)'),
        ('outgoing', 'Расход (поставщику)'),
    ]
    
    PAYMENT_METHODS = [
        ('cash', 'Наличные'),
        ('bank', 'Банковский перевод'),
        ('card', 'Банковская карта'),
        ('offset', 'Взаимозачет'),
    ]
    
    # Основная информация
    number = models.CharField('Номер платежа', max_length=50, unique=True)
    date = models.DateField('Дата платежа')
    payment_type = models.CharField('Тип', max_length=20, choices=PAYMENT_TYPES)
    
    # Связи
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, verbose_name='Счет', related_name='payments', null=True, blank=True)
    counterparty = models.ForeignKey(Counterparty, on_delete=models.PROTECT, verbose_name='Контрагент', related_name='payments')
    
    # Финансовые данные
    amount = models.DecimalField('Сумма', max_digits=15, decimal_places=2)
    payment_method = models.CharField('Способ оплаты', max_length=20, choices=PAYMENT_METHODS)
    
    # Для связи с другими блоками (когда появятся)
    external_order_id = models.CharField('ID заказа из CRM', max_length=100, blank=True, help_text='Для интеграции с блоком Сбыт')
    external_supply_request_id = models.CharField('ID заявки на снабжение', max_length=100, blank=True, help_text='Для интеграции с блоком Флот')
    
    # Комментарий
    comment = models.TextField('Комментарий', blank=True)
    
    # Метаданные
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='Кто создал')
    
    class Meta:
        verbose_name = 'Платеж'
        verbose_name_plural = 'Платежи'
        ordering = ['-date']
    
    def __str__(self):
        return f"{self.number} от {self.date.strftime('%d.%m.%Y')} - {self.amount} руб."
    
    def save(self, *args, **kwargs):
        # Если платеж привязан к счету, обновляем оплаченную сумму в счете
        if self.invoice:
            super().save(*args, **kwargs)  # сначала сохраняем платеж
            # Пересчитываем общую оплату по счету
            total_paid = self.invoice.payments.aggregate(total=models.Sum('amount'))['total'] or 0
            self.invoice.paid_amount = total_paid
            
            # Обновляем статус счета
            if self.invoice.paid_amount >= self.invoice.amount:
                self.invoice.status = 'paid'
            elif self.invoice.paid_amount > 0:
                self.invoice.status = 'sent'  # частично оплачен
            else:
                self.invoice.status = 'sent'
            
            self.invoice.save()
        else:
            super().save(*args, **kwargs)


class ExpenseCategory(models.Model):
    """Категория расходов (для аналитики)"""
    
    name = models.CharField('Название', max_length=100)
    code = models.CharField('Код', max_length=20, unique=True)
    description = models.TextField('Описание', blank=True)
    
    class Meta:
        verbose_name = 'Категория расходов'
        verbose_name_plural = 'Категории расходов'
        ordering = ['name']
    
    def __str__(self):
        return self.name


class ExpenseItem(models.Model):
    """Статья расходов (детализация)"""
    
    name = models.CharField('Название', max_length=100)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, verbose_name='Категория', related_name='items')
    is_active = models.BooleanField('Активна', default=True)
    
    class Meta:
        verbose_name = 'Статья расходов'
        verbose_name_plural = 'Статьи расходов'
        ordering = ['category', 'name']
    
    def __str__(self):
        return f"{self.category.name} - {self.name}"

class Employee(models.Model):
    """Сотрудник (для зарплаты)"""
    
    EMPLOYEE_TYPES = [
        ('shore', 'Береговой персонал'),
        ('crew', 'Экипаж судна'),
    ]
    
    # Личные данные
    full_name = models.CharField('ФИО', max_length=200)
    position = models.CharField('Должность', max_length=100)
    employee_type = models.CharField('Тип', max_length=20, choices=EMPLOYEE_TYPES)
    
    # Для экипажа (связь с флотом, когда появится)
    ship_name = models.CharField('Название судна', max_length=100, blank=True, help_text='Для экипажа')
    external_ship_id = models.CharField('ID судна из блока Флот', max_length=100, blank=True)
    
    # Финансовые данные
    base_salary = models.DecimalField('Оклад', max_digits=10, decimal_places=2, default=0)
    hourly_rate = models.DecimalField('Часовая ставка', max_digits=10, decimal_places=2, default=0)
    
    # Реквизиты для выплаты
    bank_name = models.CharField('Банк', max_length=100, blank=True)
    bank_account = models.CharField('Счет', max_length=20, blank=True)
    
    # Статус
    is_active = models.BooleanField('Работает', default=True)
    hire_date = models.DateField('Дата приема')
    fire_date = models.DateField('Дата увольнения', null=True, blank=True)
    
    # Метаданные
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    
    class Meta:
        verbose_name = 'Сотрудник'
        verbose_name_plural = 'Сотрудники'
        ordering = ['full_name']
    
    def __str__(self):
        return f"{self.full_name} - {self.position}"


class SalaryCalculation(models.Model):
    """Начисление зарплаты"""
    
    STATUSES = [
        ('draft', 'Черновик'),
        ('approved', 'Утверждено'),
        ('paid', 'Выплачено'),
    ]
    
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, verbose_name='Сотрудник', related_name='salaries')
    period_start = models.DateField('Начало периода')
    period_end = models.DateField('Конец периода')
    
    # Данные для расчета
    days_worked = models.IntegerField('Отработано дней', default=0)
    hours_worked = models.IntegerField('Отработано часов', default=0)
    
    # Начисления
    base_amount = models.DecimalField('Оклад/ставка', max_digits=10, decimal_places=2)
    bonus_amount = models.DecimalField('Премия', max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField('Итого начислено', max_digits=10, decimal_places=2)
    
    # Для экипажа (рейсовые)
    voyage_days = models.IntegerField('Дней в рейсе', default=0, help_text='Для экипажа')
    voyage_rate = models.DecimalField('Ставка за день в рейсе', max_digits=10, decimal_places=2, default=0)
    voyage_amount = models.DecimalField('Рейсовые', max_digits=10, decimal_places=2, default=0)
    
    # Статус
    status = models.CharField('Статус', max_length=20, choices=STATUSES, default='draft')
    payment_date = models.DateField('Дата выплаты', null=True, blank=True)
    
    # Комментарий
    comment = models.TextField('Комментарий', blank=True)
    
    # Метаданные
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='Кто начислил')
    
    class Meta:
        verbose_name = 'Начисление зарплаты'
        verbose_name_plural = 'Начисления зарплаты'
        ordering = ['-period_end']
    
    def __str__(self):
        return f"{self.employee.full_name} - {self.period_start} - {self.period_end}"