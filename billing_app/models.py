from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal
import uuid


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    role = models.ForeignKey('Role', on_delete=models.SET_NULL, null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return self.email


class Role(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Site(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    address = models.TextField()
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.name


class SiteAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('user', 'site')

    def __str__(self):
        return f"{self.user.email} - {self.site.name}"


class Customer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    plot_no = models.CharField(max_length=50)
    court_name = models.CharField(max_length=100)
    usage_status = models.CharField(max_length=20, choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive')])
    account_status = models.CharField(max_length=20, choices=[('ACTIVE', 'Active'), ('SUSPENDED', 'Suspended')])
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    meter = models.OneToOneField('Meter', on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Meter(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    METER_TYPES = [
        ('MANUAL', 'Manual'),
        ('SMART', 'Smart'),
    ]
    meter_number = models.CharField(max_length=50, unique=True)
    meter_type = models.CharField(max_length=20, choices=METER_TYPES)
    meter_address = models.CharField(max_length=50, blank=True, null=True, unique=True)
    imei = models.CharField(max_length=50, blank=True, null=True)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    installed_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive')])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.meter_number


class UnitPrice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    effective_date = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"${self.unit_price} effective {self.effective_date}"


class BillingRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    PAYMENT_STATUSES = [
        ('PAID', 'Paid'),
        ('PARTIAL', 'Partial'),
        ('UNPAID', 'Unpaid'),
    ]
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    meter = models.ForeignKey(Meter, on_delete=models.CASCADE)
    past_reading = models.DecimalField(max_digits=10, decimal_places=2)
    current_reading = models.DecimalField(max_digits=10, decimal_places=2)
    reading_date = models.DateTimeField(default=timezone.now)
    amount_due = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    current_amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=10, decimal_places=2)
    previous_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    current_reading_amount = models.DecimalField(max_digits=10, decimal_places=2,default=0)
    unit_price_used = models.DecimalField(max_digits=10, decimal_places=2)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUSES, default='UNPAID')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.current_reading < self.past_reading:
            raise ValidationError("Current reading cannot be less than past reading.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class PaymentLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    billing_record = models.ForeignKey(BillingRecord, on_delete=models.CASCADE,null=False,blank=False, related_name="payments")
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=50)
    transaction_reference = models.CharField(max_length=100, unique=True)
    payment_date = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Payment {self.amount_paid} for Billing {self.billing_record.id}"


class ReadingLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    billing_record = models.ForeignKey(BillingRecord, on_delete=models.CASCADE, null=False,blank=False, related_name="readings")
    previous_reading = models.DecimalField(max_digits=10, decimal_places=2)
    new_reading = models.DecimalField(max_digits=10, decimal_places=2)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Reading {self.new_reading} for Billing {self.billing_record.id}"


class SmartMeterReading(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meter = models.ForeignKey(Meter, on_delete=models.CASCADE, related_name='smart_readings')
    meter_address = models.CharField(max_length=50, db_index=True)
    reading_time = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField()
    total_flow_m3 = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    total_flow_raw = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    total_flow_unit = models.CharField(max_length=20, blank=True, null=True)
    valve_status = models.CharField(max_length=20, blank=True, null=True)
    battery_voltage_v = models.FloatField(null=True, blank=True)
    csq = models.IntegerField(null=True, blank=True)
    no_water_alarm = models.BooleanField(null=True, blank=True)
    low_battery_alarm = models.BooleanField(null=True, blank=True)
    reverse_alarm = models.BooleanField(null=True, blank=True)
    water_temperature_c = models.FloatField(null=True, blank=True)
    instantaneous_flow = models.IntegerField(null=True, blank=True)
    raw_payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-reading_time', '-received_at']
        indexes = [models.Index(fields=['meter_address', '-reading_time'])]

    def __str__(self):
        return f"Reading {self.meter_address} @ {self.reading_time}"


class PrepaidWallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='wallet')
    balance_m3 = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    last_known_flow_m3 = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    valve_status = models.CharField(max_length=20, default='unknown')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet: {self.customer} — {self.balance_m3} m³"


class ValveCommand(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    STATUS_CHOICES = [('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')]
    meter = models.ForeignKey(Meter, on_delete=models.CASCADE, related_name='valve_commands')
    action = models.CharField(max_length=20)
    reason = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    fengbo_response = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"ValveCommand {self.action} {self.meter} ({self.status})"
