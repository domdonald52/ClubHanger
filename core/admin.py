from django.contrib import admin
from .models import (
    User, Club, ClubMember, MemberCredential,
    Aircraft, AircraftMaintenanceItem,
    FlightType, ChargeRate, Account,
    Booking, BookingAuditLog, FlightCompletion,
    BlockOutType, BlockOut, InstructorAvailability,
    FeedbackMessage,
)

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('username', 'first_name', 'last_name', 'email', 'is_active')
    search_fields = ('username', 'email', 'last_name')

@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'is_active')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(ClubMember)
class ClubMemberAdmin(admin.ModelAdmin):
    list_display = ('user', 'club', 'role', 'standing', 'subscription_expires', 'is_current')
    list_filter = ('club', 'role', 'standing')
    search_fields = ('user__username', 'user__last_name', 'caa_number')
    readonly_fields = ('is_current', 'join_date')

@admin.register(MemberCredential)
class MemberCredentialAdmin(admin.ModelAdmin):
    list_display = ('club_member', 'credential_type', 'expiry_date', 'is_expired')
    list_filter = ('credential_type', 'expiry_date')
    search_fields = ('club_member__user__last_name',)

@admin.register(Aircraft)
class AircraftAdmin(admin.ModelAdmin):
    list_display = ('registration', 'aircraft_type', 'club', 'status', 'is_available_for_hire')
    list_filter = ('club', 'status')
    search_fields = ('registration',)

@admin.register(AircraftMaintenanceItem)
class AircraftMaintenanceItemAdmin(admin.ModelAdmin):
    list_display = ('aircraft', 'name', 'due_date', 'urgency')
    list_filter = ('urgency', 'aircraft')

@admin.register(FlightType)
class FlightTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'club', 'is_solo', 'is_training', 'is_billable')
    list_filter = ('club', 'is_solo', 'is_training')

@admin.register(ChargeRate)
class ChargeRateAdmin(admin.ModelAdmin):
    list_display = ('aircraft', 'flight_type', 'amount', 'time_method')
    list_filter = ('aircraft__club', 'flight_type')

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('club_member', 'balance', 'credit_limit')
    list_filter = ('club_member__club',)

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('member', 'aircraft', 'scheduled_start', 'status', 'confirmed_by')
    list_filter = ('status', 'flight_type', 'club')
    search_fields = ('member__user__last_name',)

@admin.register(BookingAuditLog)
class BookingAuditLogAdmin(admin.ModelAdmin):
    list_display = ('booking', 'event_type', 'user', 'created_at')
    list_filter = ('event_type', 'created_at')
    readonly_fields = ('created_at',)

@admin.register(FlightCompletion)
class FlightCompletionAdmin(admin.ModelAdmin):
    list_display = ('booking', 'actual_flight_hours', 'total_charge', 'payment_method')
    list_filter = ('payment_method',)

@admin.register(BlockOutType)
class BlockOutTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'club', 'is_hard', 'color')
    list_filter = ('club', 'is_hard')

@admin.register(BlockOut)
class BlockOutAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'club', 'scope', 'recurrence', 'date', 'weekday')
    list_filter = ('club', 'scope', 'recurrence')

@admin.register(InstructorAvailability)
class InstructorAvailabilityAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'recurrence', 'weekday', 'date', 'all_day', 'start_time', 'end_time')
    list_filter = ('recurrence', 'club_member__club')
    search_fields = ('club_member__user__last_name',)

@admin.register(FeedbackMessage)
class FeedbackMessageAdmin(admin.ModelAdmin):
    list_display  = ('submitted_at', 'message_type', 'sender', 'club', 'is_read')
    list_filter   = ('message_type', 'is_read', 'club')
    search_fields = ('sender__username', 'sender__last_name', 'message')
    readonly_fields = ('club', 'sender', 'message_type', 'message', 'submitted_at')
    list_editable = ('is_read',)
