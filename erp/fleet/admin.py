from django.contrib import admin
from .models import Ship, CrewMember, Voyage, Maintenance, Contractor


@admin.register(Ship)
class ShipAdmin(admin.ModelAdmin):
    list_display = ('name', 'imo_number', 'type', 'status', 'current_location')
    list_filter = ('status', 'type')
    search_fields = ('name', 'imo_number')


@admin.register(CrewMember)
class CrewMemberAdmin(admin.ModelAdmin):
    list_display = ('user', 'rank', 'assigned_ship', 'contract_end_date')
    list_filter = ('rank', 'assigned_ship')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')


@admin.register(Voyage)
class VoyageAdmin(admin.ModelAdmin):
    list_display = ('ship', 'start_date', 'end_date', 'fishing_area', 'is_completed')
    list_filter = ('is_completed', 'fishing_area')
    search_fields = ('ship__name',)


@admin.register(Maintenance)
class MaintenanceAdmin(admin.ModelAdmin):
    list_display = ('ship', 'maintenance_type', 'start_date', 'end_date', 'status', 'contractor', 'contractor_text')
    list_filter = ('status', 'maintenance_type')
    search_fields = ('ship__name', 'description')


@admin.register(Contractor)
class ContractorAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_person', 'phone', 'email', 'specialization')
    search_fields = ('name', 'contact_person', 'phone')
