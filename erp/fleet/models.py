from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Судно'
        verbose_name_plural = 'Судна'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.imo_number})"

    def get_actual_status(self, date=None):
        """
        Возвращает актуальный статус судна на указанную дату (по умолчанию сегодня).
        Приоритет:
        1. Если судно выведено из эксплуатации (ручной статус) -> 'out_of_service'
        2. Если есть активный ремонт (статус 'in_progress' и даты включают date) -> 'under_repair'
        3. Если есть активный рейс (не завершён и даты включают date) -> 'at_sea'
        4. Иначе -> 'in_port'
        """
        from django.utils import timezone
        if date is None:
            date = timezone.now().date()
        # Если судно выведено из эксплуатации (ручной статус), возвращаем его
        if self.status == 'out_of_service':
            return 'out_of_service'
        # Проверяем активный ремонт
        active_maintenance = self.maintenances.filter(
            status='in_progress',
            start_date__lte=date,
            end_date__gte=date
        ).exists()
        if active_maintenance:
            return 'under_repair'
        # Проверяем активный рейс
        active_voyage = self.voyages.filter(
            is_completed=False,
            start_date__lte=date,
            end_date__gte=date
        ).exists()
        if active_voyage:
            return 'at_sea'
        # Иначе в порту
        return 'in_port'

    @property
    def actual_status(self):
        """Свойство для получения актуального статуса на текущую дату."""
        return self.get_actual_status()

    def get_actual_status_display(self, date=None):
        """Возвращает отображаемое название актуального статуса."""
        status = self.get_actual_status(date)
        for key, label in self.STATUS_CHOICES:
            if key == status:
                return label
        return status


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

        # Если рейс завершён, не проверяем пересечения (можно пропустить)
        if self.is_completed:
            return

        # Определяем интервал рейса
        start = self.start_date
        end = self.end_date if self.end_date else timezone.now() + timezone.timedelta(days=365)  # если end_date не указан, считаем бесконечным

        # Проверка пересечения с другими рейсами этого судна (исключая текущий, если он уже существует)
        overlapping_voyages = Voyage.objects.filter(
            ship=self.ship,
            is_completed=False,
            start_date__lt=end,
            end_date__gt=start
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
            start_date__lt=end,
            end_date__gt=start
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
            end_date__gte=now.date()
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
                end_date__gt=now
            ).exclude(pk=self.pk).exists()
            if not other_active and self.ship.status == 'at_sea' and not active_maintenance:
                self.ship.status = 'in_port'
                self.ship.save(update_fields=['status'])
        # Если рейс запланирован (start_date > now) и судно "в море" из-за этого рейса (не должно быть),
        # но на всякий случай оставляем статус как есть.
        
        super().save(*args, **kwargs)


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

    class Meta:
        verbose_name = 'Техническое обслуживание/Ремонт'
        verbose_name_plural = 'Технические обслуживания/Ремонты'
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.get_maintenance_type_display()} {self.ship.name} ({self.start_date})"

    def clean(self):
        """
        Валидация пересечения дат с рейсами и другими ремонтами.
        Учитывает аварийный ремонт (emergency) - разрешает создание, но требует досрочного завершения рейса.
        """
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        # Определяем интервал ремонта
        start = self.start_date
        end = self.end_date if self.end_date else timezone.now().date() + timezone.timedelta(days=365)

        # Проверка пересечения с другими ремонтами этого судна (исключая текущий)
        overlapping_maintenances = Maintenance.objects.filter(
            ship=self.ship,
            status__in=['in_progress', 'planned'],
            start_date__lt=end,
            end_date__gt=start
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
            start_date__lt=end,
            end_date__gt=start
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
                end_date__gte=now
            ).exclude(pk=self.pk).exists()
            if not other_active:
                # Нет активных ремонтов, определяем статус судна на основе рейсов
                Voyage = apps.get_model('fleet', 'Voyage')
                # Проверяем, есть ли активные рейсы
                active_voyage = Voyage.objects.filter(
                    ship=self.ship,
                    is_completed=False,
                    start_date__lte=now,
                    end_date__gt=now
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
