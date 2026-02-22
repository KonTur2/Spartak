# accounts/forms.py
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import UserRole

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={
        'class': 'form-control',
        'placeholder': 'Email'
    }))
    
    role = forms.ChoiceField(
        choices=UserRole.ROLE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        label='Роль'
    )
    
    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2', 'role')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if field != 'role':
                field.widget.attrs['class'] = 'form-control'
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        
        if commit:
            user.save()
            # Создаем или обновляем роль пользователя
            user_role, created = UserRole.objects.get_or_create(user=user)
            user_role.role = self.cleaned_data['role']
            user_role.save()
        
        return user

class LoginForm(forms.Form):
    username = forms.CharField(max_length=100, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Имя пользователя'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control',
        'placeholder': 'Пароль'
    }))