# accounts/decorators.py
from django.http import HttpResponseForbidden
from functools import wraps

def role_required(allowed_roles=[]):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated:
                if hasattr(request.user, 'role'):
                    user_role = request.user.role.role
                    if user_role in allowed_roles:
                        return view_func(request, *args, **kwargs)
            return HttpResponseForbidden("У вас нет доступа к этой странице")
        return wrapper
    return decorator

# Контекстный процессор для добавления роли в шаблоны
def user_role(request):
    if request.user.is_authenticated:
        if hasattr(request.user, 'role'):
            return {'user_role': request.user.role.get_role_display()}
    return {'user_role': None}