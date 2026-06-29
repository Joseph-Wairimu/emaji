from django.contrib import admin
from .models import (
    User, Role, Site, SiteAssignment, Customer, Meter,
    UnitPrice, BillingRecord, PaymentLog, ReadingLog,
    SmartMeterReading, PrepaidWallet, ValveCommand,
    CardTerminalDevice, CardBinding,
    CardTerminalTransaction, CardTerminalWhitelistEntry,
)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['email', 'username', 'role', 'is_active']
    search_fields = ['email', 'username']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'description']


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['name', 'address']
    search_fields = ['name']


@admin.register(SiteAssignment)
class SiteAssignmentAdmin(admin.ModelAdmin):
    list_display = ['user', 'site']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'phone', 'account_status', 'site']
    search_fields = ['first_name', 'last_name', 'phone']
    list_filter = ['account_status', 'site']


@admin.register(Meter)
class MeterAdmin(admin.ModelAdmin):
    list_display = ['meter_number', 'meter_type', 'meter_address', 'status', 'site']
    search_fields = ['meter_number', 'meter_address']
    list_filter = ['meter_type', 'status']


@admin.register(UnitPrice)
class UnitPriceAdmin(admin.ModelAdmin):
    list_display = ['unit_price', 'effective_date']


@admin.register(BillingRecord)
class BillingRecordAdmin(admin.ModelAdmin):
    list_display = ['customer', 'meter', 'reading_date', 'amount_due', 'payment_status']
    list_filter = ['payment_status']
    search_fields = ['customer__first_name', 'customer__last_name']


@admin.register(PaymentLog)
class PaymentLogAdmin(admin.ModelAdmin):
    list_display = ['transaction_reference', 'customer', 'amount_paid', 'billing_type', 'payment_method', 'payment_date']
    list_filter = ['billing_type', 'payment_method']
    search_fields = ['transaction_reference']


@admin.register(ReadingLog)
class ReadingLogAdmin(admin.ModelAdmin):
    list_display = ['meter', 'customer', 'previous_reading', 'new_reading', 'recorded_at', 'billing_type']
    list_filter = ['billing_type']


@admin.register(SmartMeterReading)
class SmartMeterReadingAdmin(admin.ModelAdmin):
    list_display = ['meter_address', 'reading_time', 'total_flow_m3', 'valve_status', 'battery_voltage_v', 'csq']
    list_filter = ['valve_status']
    search_fields = ['meter_address']


@admin.register(PrepaidWallet)
class PrepaidWalletAdmin(admin.ModelAdmin):
    list_display = ['customer', 'balance_m3', 'balance_kes', 'valve_status', 'updated_at']
    search_fields = ['customer__first_name', 'customer__last_name']


@admin.register(ValveCommand)
class ValveCommandAdmin(admin.ModelAdmin):
    list_display = ['meter', 'action', 'reason', 'status', 'created_at']
    list_filter = ['status', 'action']


# ---------------------------------------------------------------------------
# Card terminal admin
# ---------------------------------------------------------------------------

@admin.register(CardTerminalDevice)
class CardTerminalDeviceAdmin(admin.ModelAdmin):
    list_display = ['device_number', 'name', 'iccid', 'site', 'is_active', 'last_seen_at']
    list_filter = ['is_active', 'site']
    search_fields = ['device_number', 'name', 'iccid']


@admin.register(CardBinding)
class CardBindingAdmin(admin.ModelAdmin):
    list_display = ['card_no', 'customer', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['card_no', 'customer__first_name', 'customer__last_name']
    raw_id_fields = ['customer']



@admin.register(CardTerminalTransaction)
class CardTerminalTransactionAdmin(admin.ModelAdmin):
    list_display = [
        'order_no', 'card_no', 'customer', 'mode', 'source',
        'amount_deducted_kes', 'balance_after_kes', 'is_successful', 'created_at',
    ]
    list_filter = ['mode', 'source', 'is_successful', 'created_at']
    search_fields = ['order_no', 'card_no']
    readonly_fields = [
        'order_no', 'card_no', 'customer', 'device', 'mode', 'source',
        'amount_deducted_kes', 'volume_consumed_units', 'balance_after_kes',
        'is_successful', 'failure_reason', 'created_at',
    ]


@admin.register(CardTerminalWhitelistEntry)
class CardTerminalWhitelistEntryAdmin(admin.ModelAdmin):
    list_display = ['card_no', 'operation', 'updated_at']
    list_filter = ['operation']
    search_fields = ['card_no']
