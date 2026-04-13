from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fleet', '0008_shipcrewrequirement'),
    ]

    operations = [
        migrations.AddField(
            model_name='ship',
            name='engine_hours',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='Необязательное поле для учёта наработки судна.',
                null=True,
                validators=[MinValueValidator(0)],
                verbose_name='Наработка двигателя (часы)',
            ),
        ),
        migrations.AddField(
            model_name='ship',
            name='last_repair_date',
            field=models.DateField(
                blank=True,
                help_text='Необязательное поле для последнего зафиксированного ремонта.',
                null=True,
                verbose_name='Дата последнего ремонта',
            ),
        ),
    ]
