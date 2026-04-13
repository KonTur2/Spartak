from django.urls import path
from . import views

app_name = 'fleet'

urlpatterns = [
    # Dashboard
    path('', views.fleet_dashboard, name='fleet_dashboard'),
    path('captain/', views.captain_panel, name='captain_panel'),
    # Ships
    path('ship/<int:ship_id>/', views.ship_detail, name='ship_detail'),
    path('ship/<int:ship_id>/crew/', views.ship_crew_assign, name='ship_crew_assign'),
    path('ship/create/', views.ship_create, name='ship_create'),
    path('ship/<int:ship_id>/edit/', views.ship_edit, name='ship_edit'),
    # Voyages
    path('voyage/create/', views.voyage_create, name='voyage_create'),
    path('voyage/create/<int:ship_id>/', views.voyage_create, name='voyage_create_for_ship'),
    path('voyage/<int:voyage_id>/edit/', views.voyage_edit, name='voyage_edit'),
    path('voyage/<int:voyage_id>/complete/', views.voyage_complete, name='voyage_complete'),
    # Crew
    path('crew/', views.crew_list, name='crew_list'),
    path('crew/create/', views.crew_create, name='crew_create'),
    path('crew/<int:crew_id>/edit/', views.crew_edit, name='crew_edit'),
    # Maintenance
    path('maintenance/', views.maintenance_list, name='maintenance_list'),
    path('maintenance/create/', views.maintenance_create, name='maintenance_create'),
    path('maintenance/create/<int:ship_id>/', views.maintenance_create, name='maintenance_create_for_ship'),
    path('maintenance/<int:maintenance_id>/', views.maintenance_detail, name='maintenance_detail'),
    path('maintenance/<int:maintenance_id>/edit/', views.maintenance_edit, name='maintenance_edit'),
    path('maintenance/<int:maintenance_id>/complete/', views.maintenance_complete, name='maintenance_complete'),
    # Maintenance logs
    path('ship/<int:ship_id>/maintenance-logs/', views.maintenance_log_list, name='maintenance_log_list'),
    path('captain-messages/', views.captain_messages_list, name='captain_messages_list'),
    # Reports
    path('maintenance/report/', views.maintenance_report, name='maintenance_report'),
    path('performance/', views.fleet_performance_report, name='performance_report'),
    path('crew/manifest/', views.crew_manifest, name='crew_manifest'),
    path('report/health/', views.fleet_health_report, name='fleet_health_report'),
    # Contractors
    path('contractors/', views.contractor_list, name='contractor_list'),
    path('contractors/create/', views.contractor_create, name='contractor_create'),
    path('contractors/<int:contractor_id>/edit/', views.contractor_edit, name='contractor_edit'),
    path('contractors/<int:contractor_id>/delete/', views.contractor_delete, name='contractor_delete'),
    path('notifications/', views.notifications_list, name='notifications_list'),
    path('notifications/<int:notification_id>/', views.notification_detail, name='notification_detail'),
]
