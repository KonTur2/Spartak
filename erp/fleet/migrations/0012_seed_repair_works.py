from django.db import migrations


DEFAULT_REPAIR_WORKS = (
    ('engine', 'Двигатель', 1200000),
    ('hull', 'Корпус', 900000),
    ('propulsion', 'Винто-рулевая группа', 750000),
    ('navigation', 'Навигационное оборудование', 450000),
    ('electrical', 'Электрооборудование', 380000),
    ('refrigeration', 'Холодильное оборудование', 520000),
    ('deck', 'Палубные механизмы', 410000),
    ('fishing', 'Промысловое оборудование', 680000),
)


def seed_repair_works(apps, schema_editor):
    RepairWork = apps.get_model('fleet', 'RepairWork')
    for code, name, base_cost in DEFAULT_REPAIR_WORKS:
        RepairWork.objects.update_or_create(
            code=code,
            defaults={
                'name': name,
                'base_cost': base_cost,
            },
        )


def unseed_repair_works(apps, schema_editor):
    RepairWork = apps.get_model('fleet', 'RepairWork')
    RepairWork.objects.filter(code__in=[code for code, _, _ in DEFAULT_REPAIR_WORKS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('fleet', '0011_repairwork_maintenance_repair_works'),
    ]

    operations = [
        migrations.RunPython(seed_repair_works, unseed_repair_works),
    ]
