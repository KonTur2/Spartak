# accounts/views.py
from datetime import datetime

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import CustomUserCreationForm, LoginForm


def login_view(request):
    # Если пользователь уже авторизован, перенаправляем на соответствующий раздел
    if request.user.is_authenticated:
        return redirect(get_role_based_redirect(request.user))

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'Добро пожаловать, {user.username}!')
                return redirect(get_role_based_redirect(user))
            else:
                messages.error(request, 'Неверное имя пользователя или пароль')
    else:
        form = LoginForm()

    context = {
        'form': form,
        'now': datetime.now(),
    }
    return render(request, 'accounts/login.html', context)


def get_role_based_redirect(user):
    """Возвращает URL для перенаправления в зависимости от роли пользователя."""
    if hasattr(user, 'role'):
        role = user.role.role
        if role == 'director':
            return reverse('dashboard')
        elif role == 'accountant':
            return reverse('finance')
        elif role == 'logistician':
            return reverse('logistics')
        elif role == 'fleet_manager':
            return reverse('fleet')
        elif role == 'crew':
            return reverse('fleet')
        elif role == 'captain':
            return reverse('fleet:captain_panel')
        elif role == 'dispatcher':
            return reverse('fleet')
        elif role == 'engineer':
            return reverse('fleet')
    return reverse('dashboard')


def register_view(request):
    # Если пользователь уже авторизован, перенаправляем
    if request.user.is_authenticated:
        return redirect(get_role_based_redirect(request.user))

    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get('username')
            messages.success(request, f'Аккаунт для {username} успешно создан!')
            login(request, user)
            return redirect(get_role_based_redirect(user))
    else:
        form = CustomUserCreationForm()

    context = {
        'form': form,
        'now': datetime.now(),
    }
    return render(request, 'accounts/register.html', context)


@login_required
def logout_view(request):
    logout(request)
    messages.info(request, 'Вы вышли из системы')
    return redirect('login')


@login_required
def profile_view(request):
    context = {
        'user_role': request.user.role.get_role_display() if hasattr(request.user, 'role') else 'Не назначена',
        'now': datetime.now(),
    }
    return render(request, 'accounts/profile.html', context)


# Временные заглушки для ролевых страниц
@login_required
def finance_view(request):
    return render(request, 'erp_app/finance.html', {'title': 'Управление финансами'})


@login_required
def logistics_view(request):
    return render(request, 'erp_app/logistics.html', {'title': 'Логистика'})


@login_required
def fleet_view(request):
    return render(request, 'erp_app/fleet.html', {'title': 'Управление флотом'})
