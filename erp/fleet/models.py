from datetime import datetime, time

from django.db import models
from django.db.models.signals import m2m_changed
from django.db.models import Q
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.dispatch import receiver
from django.utils import timezone


class Ship(models.Model):
    SHIP_TYPE_CHOICES = [
        ('fishing', 'Промысловое'),
        ('transport', 'Транспортное'),
        ('passenger', 'Пассажирское'),
    ]
    STATUS_CHOICES = [
        ('at_sea', 'В море'),
        ('in_port', 'В порту'),
        ('under_repair', 'На ремонте'),
        ('out_of_service', 'Выведено из эксплуатации'),
    ]

    name = models.CharField('Название судна', max_length=200)
    type = models.CharField('Тип судна', max_length=20, choices=SHIP_TYPE_CHOICES, default='fishing')
    imo_number = models.CharField('Номер ИМО', max_length=20, unique=True)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default='in_port')
    current_location = models.CharField('Текущее местоположение', max_length=300, blank=True)
    technical_condition = models.TextField('Техническое состояние', blank=True)
    engine_hours = models.PositiveIntegerField(
        'Наработка двигателя (часы)',
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
        help_text='Необязательное поле для учёта наработки судна.',
    )
    last_repair_date = models.DateField(
        'Дата последнего ремонта',
        null=True,
        blank=True,
        help_text='Необязательное поле для последнего зафиксированного ремонта.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Судно'
        verbose_name_plural = 'Судна'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.imo_number})"

    def get_actual_status(self, date=None):
        from .services import get_ship_operational_state

        return get_ship_operational_state(self, at_datetime=date).actual_status

    @property
    def actual_status(self):
        """Свойство для получения актуального статуса на текущую дату."""
        return self.get_actual_status()

    def get_actual_status_display(self, date=None):
        from .services import get_ship_operational_state

        return get_ship_operational_state(self, at_datetime=date).status_display


class MaintenanceLog(models.Model):
    ship = models.ForeignKey(
        Ship,
        on_delete=models.CASCADE,
        related_name='maintenance_logs',
        verbose_name='Судно',
    )
    recorded_at = models.DateTimeField('Дата и время записи', default=timezone.now)
    engine_hours = models.PositiveIntegerField(
        'Наработка двигателя (часы)',
        validators=[MinValueValidator(0)],
    )
    note = models.TextField('Заметка', blank=True)
    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='maintenance_logs',
        verbose_name='Автор записи',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Журнал технических сообщений'
        verbose_name_plural = 'Журнал технических сообщений'
        ordering = ['-recorded_at', '-id']

    def __str__(self):
        return f'{self.ship.name} @ {self.recorded_at:%d.%m.%Y %H:%M}'

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()

        if self.engine_hours is None:
            return

        latest_entry = (
            MaintenanceLog.objects.filter(ship=self.ship)
            .exclude(pk=self.pk)
            .order_by('-recorded_at', '-id')
            .first()
        )
        if latest_entry and self.engine_hours < latest_entry.engine_hours:
            raise ValidationError({
                'engine_hours': (
                    'Новая наработка не может быть меньше последней записи '
                    f'по судну ({latest_entry.engine_hours} ч).'
                )
            })
    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            from .services import process_maintenance_log_event

            process_maintenance_log_event(self)


class CrewMember(models.Model):
    RANK_CHOICES = [
        ('captain', 'Капитан'),
        ('first_mate', 'Старший помощник'),
        ('engineer', 'Механик'),
        ('seaman', 'Матрос'),
        ('cook', 'Кок'),
        ('other', 'Другое'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='crew_profile', verbose_name='Пользователь')
    rank = models.CharField('Должность/Звание', max_length=20, choices=RANK_CHOICES, default='seaman')
    assigned_ship = models.ForeignKey(Ship, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='crew_members', verbose_name='Закрепленное судно')
    contract_end_date = models.DateField('Дата окончания контракта', null=True, blank=True)
    notes = models.TextField('Примечания', blank=True)

    class Meta:
        verbose_name = 'Член экипажа'
        verbose_name_plural = 'Члены экипажа'
        ordering = ['rank', 'user__last_name']

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.get_rank_display()})"

    def clean(self):
        super().clean()
        if self.rank == 'captain' and self.assigned_ship_id:
            duplicate_captain_exists = CrewMember.objects.filter(
                rank='captain',
                assigned_ship_id=self.assigned_ship_id,
            ).exclude(pk=self.pk).exists()
            if duplicate_captain_exists:
                raise ValidationError({
                    'assigned_ship': 'На судне уже назначен капитан. Сначала снимите текущее назначение.',
                })

    def get_contract_status(self, date=None):
        """
        Возвращает статус контракта на указанную дату (по умолчанию сегодня).
        Возможные значения:
        - 'expired': контракт истёк (contract_end_date < date)
        - 'expires_soon_7': истекает в ближайшие 7 дней (включая сегодня)
        - 'expires_soon_30': истекает в ближайшие 30 дней (но не в ближайшие 7)
        - 'active': активен (более 30 дней до окончания)
        - 'no_contract': нет даты окончания контракта
        """
        from django.utils import timezone
        if date is None:
            date = timezone.now().date()
        
        if not self.contract_end_date:
            return 'no_contract'
        
        days_left = (self.contract_end_date - date).days
        
        if days_left < 0:
            return 'expired'
        elif days_left <= 7:
            return 'expires_soon_7'
        elif days_left <= 30:
            return 'expires_soon_30'
        else:
            return 'active'

    @property
    def contract_status(self):
        """Свойство для получения статуса контракта на текущую дату."""
        return self.get_contract_status()

    def get_contract_status_display(self, date=None):
        """Возвращает отображаемое название статуса контракта."""
        status = self.get_contract_status(date)
        status_map = {
            'expired': 'Истёк',
            'expires_soon_7': 'Истекает в ближайшие 7 дней',
            'expires_soon_30': 'Истекает в ближайшие 30 дней',
            'active': 'Активен',
            'no_contract': 'Без контракта',
        }
        return status_map.get(status, status)

    def is_contract_expired(self, date=None):
        """Проверяет, истёк ли контракт на указанную дату."""
        return self.get_contract_status(date) == 'expired'

    def is_contract_expiring_soon(self, days=7, date=None):
        """Проверяет, истекает ли контракт в ближайшие N дней."""
        from django.utils import timezone
        if date is None:
            date = timezone.now().date()
        
        if not self.contract_end_date:
            return False
        
        days_left = (self.contract_end_date - date).days
        return 0 <= days_left <= days


class ShipCrewRequirement(models.Model):
    ship_type = models.CharField('Тип судна', max_length=20, choices=Ship.SHIP_TYPE_CHOICES)
    role = models.CharField('Роль экипажа', max_length=20, choices=CrewMember.RANK_CHOICES)
    required_count = models.PositiveIntegerField(
        'Требуемое количество',
        validators=[MinValueValidator(1)],
        default=1,
    )

    class Meta:
        verbose_name = 'Требование к составу экипажа'
        verbose_name_plural = 'Требования к составу экипажа'
        ordering = ['ship_type', 'role']
        constraints = [
            models.UniqueConstraint(
                fields=['ship_type', 'role'],
                name='fleet_ship_crew_requirement_unique_role_per_type',
            )
        ]

    def __str__(self):
        ship_type_label = dict(Ship.SHIP_TYPE_CHOICES).get(self.ship_type, self.ship_type)
        role_label = dict(CrewMember.RANK_CHOICES).get(self.role, self.role)
        return f'{ship_type_label}: {role_label} x{self.required_count}'


class Voyage(models.Model):
    FISHING_AREA_CHOICES = [
        ('okhotsk', 'Охотское море'),
        ('bering', 'Берингово море'),
        ('japan', 'Японское море'),
        ('pacific', 'Тихий океан'),
        ('atlantic', 'Атлантический океан'),
        ('other', 'Другое'),
    ]

    ship = models.ForeignKey(Ship, on_delete=models.CASCADE, related_name='voyages', verbose_name='Судно')
    crew = models.ManyToManyField(CrewMember, related_name='voyages', verbose_name='Экипаж', blank=True)
    start_date = models.DateTimeField('Дата выхода', default=timezone.now)
    end_date = models.DateTimeField('Дата возвращения', null=True, blank=True)
    fishing_area = models.CharField('Район промысла', max_length=30, choices=FISHING_AREA_CHOICES, default='okhotsk')
    catch_plan = models.FloatField('План добычи (тонны)', validators=[MinValueValidator(0)], default=0)
    actual_catch = models.FloatField('Фактическая добыча (тонны)', validators=[MinValueValidator(0)], null=True, blank=True)
    description = models.TextField('Описание рейса', blank=True)
    is_completed = models.BooleanField('Завершен', default=False)

    class Meta:
        verbose_name = 'Рейс'
        verbose_name_plural = 'Рейсы'
        ordering = ['-start_date']

    def __str__(self):
        return f"Рейс {self.ship.name} от {self.start_date.strftime('%d.%m.%Y')}"

    def duration_days(self):
        if self.end_date:
            delta = self.end_date - self.start_date
            return delta.days
        return None

    def clean(self):
        """
        Валидация пересечения дат с другими рейсами и ремонтами.
        Вызывается в формах перед сохранением.
        """
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        if self.end_date and self.end_date <= self.start_date:
            raise ValidationError('Дата возвращения должна быть позже даты выхода.')

        # Если рейс завершён, не проверяем пересечения (можно пропустить)
        if self.is_completed:
            return

        # Определяем интервал рейса
        start = self.start_date
        end = self.end_date if self.end_date else timezone.now() + timezone.timedelta(days=3650)

        # Проверка пересечения с другими рейсами этого судна (исключая текущий, если он уже существует)
        overlapping_voyages = Voyage.objects.filter(
            ship=self.ship,
            is_completed=False,
            start_date__lt=end,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gt=start)
        ).exclude(pk=self.pk)
        if overlapping_voyages.exists():
            raise ValidationError(
                f'Судно "{self.ship.name}" уже находится в рейсе в указанный период. '
                f'Пересекается с рейсом(ами): {", ".join(str(v) for v in overlapping_voyages[:3])}.'
            )

        # Проверка пересечения с ремонтами этого судна (статус 'in_progress' или 'planned' с датами, которые пересекаются)
        overlapping_maintenances = Maintenance.objects.filter(
            ship=self.ship,
            status__in=['in_progress', 'planned'],
            start_date__lt=end.date(),
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gt=start.date())
        )
        if overlapping_maintenances.exists():
            raise ValidationError(
                f'Судно "{self.ship.name}" находится на ремонте в указанный период. '
                f'Пересекается с ремонтом(ами): {", ".join(str(m) for m in overlapping_maintenances[:3])}.'
            )

        # Дополнительная проверка: если судно выведено из эксплуатации, нельзя создавать рейс
        if self.ship.status == 'out_of_service':
            raise ValidationError(
                f'Судно "{self.ship.name}" выведено из эксплуатации. Создание рейса невозможно.'
            )

    def save(self, *args, **kwargs):
        from django.utils import timezone
        from django.apps import apps
        now = timezone.now()
        
        # Преобразуем наивные datetime в aware, если необходимо
        if timezone.is_naive(self.start_date):
            self.start_date = timezone.make_aware(self.start_date)
        if self.end_date and timezone.is_naive(self.end_date):
            self.end_date = timezone.make_aware(self.end_date)
        
        # Получаем модель Maintenance
        Maintenance = apps.get_model('fleet', 'Maintenance')
        # Проверяем, есть ли активный ремонт у судна
        active_maintenance = Maintenance.objects.filter(
            ship=self.ship,
            status='in_progress',
            start_date__lte=now.date(),
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=now.date())
        ).exists()
        
        # Определяем, является ли рейс активным в данный момент
        is_active = (
            not self.is_completed
            and self.start_date <= now
            and (self.end_date is None or self.end_date > now)
        )
        
        # Определяем, является ли рейс завершённым (прошёл или отмечен как завершённый)
        is_finished = (
            self.is_completed
            or (self.end_date is not None and self.end_date <= now)
        )
        
        # Если рейс активен и нет активного ремонта, судно должно быть "в море"
        if is_active and not active_maintenance:
            if self.ship.status != 'at_sea':
                self.ship.status = 'at_sea'
                self.ship.save(update_fields=['status'])
        # Если рейс завершён и судно сейчас "в море", меняем на "в порту"
        # (но только если нет других активных рейсов у этого судна и нет активного ремонта)
        elif is_finished:
            # Проверяем, есть ли у судна другие активные рейсы
            other_active = Voyage.objects.filter(
                ship=self.ship,
                is_completed=False,
                start_date__lte=now,
            ).filter(
                Q(end_date__isnull=True) | Q(end_date__gt=now)
            ).exclude(pk=self.pk).exists()
            if not other_active and self.ship.status == 'at_sea' and not active_maintenance:
                self.ship.status = 'in_port'
                self.ship.save(update_fields=['status'])
        # Если рейс запланирован (start_date > now) и судно "в море" из-за этого рейса (не должно быть),
        # но на всякий случай оставляем статус как есть.
        
        super().save(*args, **kwargs)


class Notification(models.Model):
    ENTITY_TYPE_CHOICES = [
        ('ship', 'Судно'),
        ('voyage', 'Рейс'),
        ('maintenance', 'Ремонт'),
        ('crew', 'Экипаж'),
    ]

    SEVERITY_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('blocking', 'Blocking'),
    ]

    entity_type = models.CharField('Тип сущности', max_length=20, choices=ENTITY_TYPE_CHOICES)
    entity_id = models.PositiveIntegerField('ID сущности')
    severity = models.CharField('Severity', max_length=20, choices=SEVERITY_CHOICES)
    code = models.CharField('Код проблемы', max_length=100)
    title = models.CharField('Заголовок', max_length=200)
    message = models.TextField('Сообщение')
    is_read = models.BooleanField('Прочитано', default=False)
    is_resolved = models.BooleanField('Решено', default=False)
    resolved_at = models.DateTimeField('Дата решения', null=True, blank=True)
    context = models.JSONField('Контекст', default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'
        ordering = ['is_resolved', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['entity_type', 'entity_id', 'code'],
                name='fleet_notification_unique_problem',
            )
        ]

    def __str__(self):
        return f'{self.get_entity_type_display()} #{self.entity_id}: {self.title}'


class Contractor(models.Model):
    """Внешний контрагент, выполняющий ремонт."""
    name = models.CharField('Название организации', max_length=200)
    contact_person = models.CharField('Контактное лицо', max_length=100, blank=True)
    phone = models.CharField('Телефон', max_length=20, blank=True)
    email = models.EmailField('Email', blank=True)
    specialization = models.CharField('Специализация', max_length=200, blank=True)
    notes = models.TextField('Примечания', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Контрагент'
        verbose_name_plural = 'Контрагенты'
        ordering = ['name']

    def __str__(self):
        return self.name


class RepairWork(models.Model):
    WORK_CODE_CHOICES = [
        ('engine', 'Двигатель'),
        ('hull', 'Корпус'),
        ('propulsion', 'Винто-рулевая группа'),
        ('navigation', 'Навигационное оборудование'),
        ('electrical', 'Электрооборудование'),
        ('refrigeration', 'Холодильное оборудование'),
        ('deck', 'Палубные механизмы'),
        ('fishing', 'Промысловое оборудование'),
    ]

    code = models.CharField('Код работы', max_length=30, choices=WORK_CODE_CHOICES, unique=True)
    name = models.CharField('Наименование работы', max_length=120)
    base_cost = models.DecimalField(
        'Базовая стоимость (руб.)',
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        default=0,
    )

    class Meta:
        verbose_name = 'Ремонтная работа'
        verbose_name_plural = 'Ремонтные работы'
        ordering = ['name']

    def __str__(self):
        return self.name


class Maintenance(models.Model):
    MAINTENANCE_TYPE_CHOICES = [
        ('planned', 'Плановое'),
        ('emergency', 'Аварийное'),
        ('current', 'Текущий ремонт'),
    ]
    
    STATUS_CHOICES = [
        ('planned', 'Запланировано'),
        ('in_progress', 'В работе'),
        ('completed', 'Завершено'),
    ]

    ship = models.ForeignKey(Ship, on_delete=models.CASCADE, related_name='maintenances', verbose_name='Судно')
    maintenance_type = models.CharField('Тип ремонта', max_length=20, choices=MAINTENANCE_TYPE_CHOICES, default='planned')
    description = models.TextField('Описание работ')
    start_date = models.DateField('Дата начала', default=timezone.now)
    end_date = models.DateField('Дата окончания', null=True, blank=True)
    cost = models.DecimalField('Стоимость (руб.)', max_digits=12, decimal_places=2, validators=[MinValueValidator(0)], default=0)
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default='planned')
    contractor = models.ForeignKey(Contractor, on_delete=models.SET_NULL, null=True, blank=True,
                                   verbose_name='Подрядчик', related_name='maintenances')
    contractor_text = models.CharField('Подрядчик (текст)', max_length=200, blank=True,
                                       help_text='Если контрагент не выбран, можно указать вручную')
    documents = models.FileField('Документы', upload_to='maintenance_docs/', blank=True, null=True)
    repair_works = models.ManyToManyField(
        RepairWork,
        blank=True,
        related_name='maintenances',
        verbose_name='Состав работ',
    )

    class Meta:
        verbose_name = 'Техническое обслуживание/Ремонт'
        verbose_name_plural = 'Технические обслуживания/Ремонты'
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.get_maintenance_type_display()} {self.ship.name} ({self.start_date})"

    def recalculate_cost(self, commit=True):
        total = sum(work.base_cost for work in self.repair_works.all())
        if total:
            self.cost = total
            if commit and self.pk:
                type(self).objects.filter(pk=self.pk).update(cost=total)
        return total

    def clean(self):
        """
        Валидация пересечения дат с рейсами и другими ремонтами.
        Учитывает аварийный ремонт (emergency) - разрешает создание, но требует досрочного завершения рейса.
        """
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        if self.end_date and self.end_date < self.start_date:
            raise ValidationError('Дата окончания ремонта не может быть раньше даты начала.')

        # Определяем интервал ремонта
        start = self.start_date
        end = self.end_date if self.end_date else timezone.now().date() + timezone.timedelta(days=3650)

        # Проверка пересечения с другими ремонтами этого судна (исключая текущий)
        overlapping_maintenances = Maintenance.objects.filter(
            ship=self.ship,
            status__in=['in_progress', 'planned'],
            start_date__lt=end,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gt=start)
        ).exclude(pk=self.pk)
        if overlapping_maintenances.exists():
            raise ValidationError(
                f'Судно "{self.ship.name}" уже имеет запланированный или выполняющийся ремонт в указанный период. '
                f'Пересекается с ремонтом(ами): {", ".join(str(m) for m in overlapping_maintenances[:3])}.'
            )

        # Проверка пересечения с рейсами этого судна
        overlapping_voyages = Voyage.objects.filter(
            ship=self.ship,
            is_completed=False,
            start_date__lt=timezone.make_aware(datetime.combine(end, time.max)),
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gt=timezone.make_aware(datetime.combine(start, time.min)))
        )
        if overlapping_voyages.exists():
            # Если это аварийный ремонт, разрешаем, но предупреждаем о необходимости досрочного завершения рейса
            if self.maintenance_type == 'emergency':
                # Можно добавить логику для автоматического завершения рейса или предупреждения
                # Пока просто предупреждаем через сообщение (не блокируем сохранение)
                # Для этого мы не вызываем ValidationError, но можно добавить поле non_field_errors
                # Однако clean должен либо пройти, либо вызвать ValidationError.
                # Поскольку аварийный ремонт разрешён, мы пропускаем ошибку, но нужно уведомить пользователя.
                # Лучше добавить предупреждение через messages в view, а здесь просто пропустить.
                pass
            else:
                raise ValidationError(
                    f'Судно "{self.ship.name}" находится в рейсе в указанный период. '
                    f'Пересекается с рейсом(ами): {", ".join(str(v) for v in overlapping_voyages[:3])}. '
                    'Для аварийного ремонта это допустимо, но требуется досрочное завершение рейса.'
                )

        # Дополнительная проверка: если судно выведено из эксплуатации, ремонт возможен только аварийный?
        if self.ship.status == 'out_of_service' and self.maintenance_type != 'emergency':
            raise ValidationError(
                f'Судно "{self.ship.name}" выведено из эксплуатации. '
                'Ремонт возможен только аварийный (emergency).'
            )

    def save(self, *args, **kwargs):
        from django.utils import timezone
        from django.apps import apps
        now = timezone.now().date()

        # Определяем, является ли ремонт активным в данный момент
        # Активным считается ремонт со статусом 'in_progress' и даты, включающие сегодня
        is_active = (
            self.status == 'in_progress'
            and self.start_date <= now
            and (self.end_date is None or self.end_date >= now)
        )

        # Определяем, является ли ремонт завершённым (статус 'completed' или дата окончания прошла)
        is_finished = (
            self.status == 'completed'
            or (self.end_date is not None and self.end_date < now)
        )

        # Если ремонт активен, судно должно быть "на ремонте"
        if is_active:
            if self.ship.status != 'under_repair':
                self.ship.status = 'under_repair'
                self.ship.save(update_fields=['status'])
        # Если ремонт завершён, проверяем, нет ли других активных ремонтов у этого судна
        elif is_finished:
            other_active = Maintenance.objects.filter(
                ship=self.ship,
                status='in_progress',
                start_date__lte=now,
            ).filter(
                Q(end_date__isnull=True) | Q(end_date__gte=now)
            ).exclude(pk=self.pk).exists()
            if not other_active:
                # Нет активных ремонтов, определяем статус судна на основе рейсов
                Voyage = apps.get_model('fleet', 'Voyage')
                # Проверяем, есть ли активные рейсы
                active_voyage = Voyage.objects.filter(
                    ship=self.ship,
                    is_completed=False,
                    start_date__date__lte=now,
                ).filter(
                    Q(end_date__isnull=True) | Q(end_date__date__gt=now)
                ).exists()
                if active_voyage:
                    new_status = 'at_sea'
                else:
                    new_status = 'in_port'
                if self.ship.status != new_status:
                    self.ship.status = new_status
                    self.ship.save(update_fields=['status'])
        # Если ремонт запланирован (status='planned' и start_date > now) - не меняем статус судна
        # (судно остаётся в текущем статусе, возможно, в порту или в море)

        super().save(*args, **kwargs)

        if self.status == 'completed' and self.maintenance_type == 'planned':
            repair_date = self.end_date or self.start_date
            if repair_date and self.ship.last_repair_date != repair_date:
                self.ship.last_repair_date = repair_date
                self.ship.save(update_fields=['last_repair_date'])
            if repair_date:
                from .services import ensure_planned_maintenance_for_ship

                ensure_planned_maintenance_for_ship(self.ship, reference_date=repair_date)

@receiver(m2m_changed, sender=Maintenance.repair_works.through)
def maintenance_repair_works_changed(sender, instance, action, **kwargs):
    if action in {'post_add', 'post_remove', 'post_clear'}:
        instance.recalculate_cost(commit=True)

