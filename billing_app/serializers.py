from rest_framework import serializers
from .models import User, Role, Site, SiteAssignment, Customer, Meter, UnitPrice, BillingRecord, PaymentLog, ReadingLog
from django.db import transaction
from decimal import Decimal
from django.utils import timezone


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'role']


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ['id', 'name', 'description']


class SiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Site
        fields = ['id', 'name', 'address', 'latitude', 'longitude']


class SiteAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteAssignment
        fields = ['id', 'user', 'site']


class CustomerSerializer(serializers.ModelSerializer):
    latest_billing = serializers.SerializerMethodField()
    site = serializers.CharField(source='site.name', read_only=True)
    site_id = serializers.UUIDField(source='site.id', read_only=True)
    class Meta:
        model = Customer
        fields = ['id', 'first_name', 'last_name', 'phone', 'email', 'plot_no', 'court_name',
                  'usage_status', 'account_status', 'site', 'meter', 'created_by', 'created_at', 'latest_billing']

    def get_latest_billing(self, obj):
        latest = obj.billingrecord_set.order_by('-reading_date').first()
        if latest:
            return {
                'current_balance': str(latest.balance),
                'last_reading_date': latest.reading_date,
                'amount_due': str(latest.amount_due),
                'payment_status': latest.payment_status,
            }
        return None


class MeterSerializer(serializers.ModelSerializer):
    site = serializers.CharField(source='site.name', read_only=True)
    site_id = serializers.UUIDField(source='site.id', read_only=True)
    class Meta:
        model = Meter
        fields = ['id', 'meter_number', 'meter_type', 'site', 'installed_at', 'status']


class UnitPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitPrice
        fields = ['id', 'unit_price', 'effective_date']


class BillingRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingRecord
        fields = ['id', 'customer', 'meter', 'past_reading', 'current_reading', 'reading_date',
                  'amount_due', 'amount_paid', 'balance', 'unit_price_used', 'payment_status', 'created_by', 'created_at']

    def validate(self, data):
        if data['current_reading'] < data['past_reading']:
            raise serializers.ValidationError("Current reading cannot be less than past reading.")
        return data

    @transaction.atomic
    def create(self, validated_data):
        customer = validated_data['customer']
        meter = validated_data['meter']
        reading_date = validated_data.get('reading_date', timezone.now())
        amount_paid = validated_data.get('amount_paid', Decimal('0.00'))

        # Get latest unit price
        unit_price = UnitPrice.objects.filter(effective_date__lte=reading_date).order_by('-effective_date').first()
        if not unit_price:
            raise serializers.ValidationError("No unit price available for the given reading date.")

        # Calculate consumption and amount due
        consumption = validated_data['current_reading'] - validated_data['past_reading']
        amount_due = consumption * unit_price.unit_price

        # Get previous balance
        previous_billing = customer.billingrecord_set.order_by('-reading_date').first()
        previous_balance = previous_billing.balance if previous_billing else Decimal('0.00')

        # Calculate new balance
        validated_data['amount_due'] = amount_due
        validated_data['unit_price_used'] = unit_price.unit_price
        validated_data['balance'] = previous_balance + amount_due - amount_paid
        validated_data['payment_status'] = (
            'PAID' if validated_data['balance'] == 0 else 'PARTIAL' if amount_paid > 0 else 'UNPAID'
        )
        validated_data['created_by'] = self.context['request'].user

        return super().create(validated_data)


class PaymentLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentLog
        fields = ['id', 'customer', 'billing_record', 'amount_paid', 'payment_method',
                  'transaction_reference', 'payment_date', 'created_by', 'created_at']

    @transaction.atomic
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        payment = super().create(validated_data)

        # Update billing record balance
        if payment.billing_record:
            billing = payment.billing_record
            billing.amount_paid += payment.amount_paid
            billing.balance -= payment.amount_paid
            billing.payment_status = 'PAID' if billing.balance == 0 else 'PARTIAL'
            billing.save()

        return payment


class ReadingLogSerializer(serializers.ModelSerializer):
    meter = serializers.CharField(source='meter.meter_number', read_only=True)
    customer = serializers.CharField(source='customer.first_name', read_only=True)
    customer_id = serializers.CharField(source='customer.id', read_only=True)
    meter_id = serializers.CharField(source='meter.id', read_only=True)
    class Meta:
        model = ReadingLog
        fields = ['id', 'customer', 'meter', 'previous_reading', 'new_reading', 'recorded_by', 'recorded_at', 'note']

    def create(self, validated_data):
        validated_data['recorded_by'] = self.context['request'].user
        return super().create(validated_data)