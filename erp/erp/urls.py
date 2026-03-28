from django.contrib import admin
from django.urls import path, include
from accounts import views as accounts_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', accounts_views.login_view, name='login'),
    path('register/', accounts_views.register_view, name='register'),
    path('logout/', accounts_views.logout_view, name='logout'),
    path('profile/', accounts_views.profile_view, name='profile'),
    path('', include('erp_app.urls')), 
    path('finance/', include('finance_app.urls')),
    path('fleet/', include('fleet.urls')),
]