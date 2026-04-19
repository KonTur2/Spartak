from django.contrib import admin
from .models import Contractor, CrewMember, Maintenance, MaintenanceLog, RepairWork, Ship, Voyage


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
    filter_horizontal = ('repair_works',)


@admin.register(Contractor)
class ContractorAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_person', 'phone', 'email', 'specialization')
    search_fields = ('name', 'contact_person', 'phone')


@admin.register(MaintenanceLog)
class MaintenanceLogAdmin(admin.ModelAdmin):
    list_display = ('ship', 'recorded_at', 'engine_hours', 'author')
    list_filter = ('ship', 'author')
    search_fields = ('ship__name', 'note', 'author__username', 'author__first_name', 'author__last_name')
    autocomplete_fields = ('ship', 'author')


@admin.register(RepairWork)
class RepairWorkAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'base_cost')
    search_fields = ('name', 'code')
