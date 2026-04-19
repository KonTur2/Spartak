# accounts/models.py
from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserRole(models.Model):
    ROLE_CHOICES = [
        ('director', 'Директор'),
        ('accountant', 'Бухгалтер'),
        ('logistician', 'Логист'),
        ('crew', 'Экипаж судна'),
        ('captain', 'Капитан'),
        ('fleet_manager', 'Управляющий флотом'),
        ('dispatcher', 'Диспетчер'),
        ('engineer', 'Инженер'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='role')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='crew')

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"


# Сигнал для автоматического создания профиля роли при создании пользователя
@receiver(post_save, sender=User)
def create_user_role(sender, instance, created, **kwargs):
    if created:
        UserRole.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_role(sender, instance, **kwargs):
    if hasattr(instance, 'role'):
        instance.role.save()
