import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('fleet', '0009_ship_engine_hours_last_repair_date'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaintenanceLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('recorded_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Дата и время записи')),
                ('engine_hours', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(0)], verbose_name='Наработка двигателя (часы)')),
                ('note', models.TextField(blank=True, verbose_name='Заметка')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('author', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='maintenance_logs', to=settings.AUTH_USER_MODEL, verbose_name='Автор записи')),
                ('ship', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='maintenance_logs', to='fleet.ship', verbose_name='Судно')),
            ],
            options={
                'verbose_name': 'Журнал технических сообщений',
                'verbose_name_plural': 'Журнал технических сообщений',
                'ordering': ['-recorded_at', '-id'],
            },
        ),
    ]
