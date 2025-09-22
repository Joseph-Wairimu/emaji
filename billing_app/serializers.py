import uuid
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
    site = serializers.CharField(source='site.name', read_only=True)
    site_id = serializers.UUIDField(source='site.id', read_only=True)
    user = serializers.CharField(source='user.email', read_only=True)
    user_id = serializers.UUIDField(source='user.id', read_only=True)
    class Meta:
        model = SiteAssignment
        fields = ['id', 'user', 'site','site_id','user_id']


class CustomerSerializer(serializers.ModelSerializer):
    latest_billing = serializers.SerializerMethodField()
    site = serializers.CharField(source='site.name', read_only=True)
    site_id = serializers.UUIDField(source='site.id', read_only=True)
    meter= serializers.CharField(source='meter.meter_number', read_only=True)
    meter_id = serializers.UUIDField(source='meter.id', read_only=True)
    class Meta:
        model = Customer
        fields = ['id', 'first_name', 'last_name', 'phone', 'email', 'plot_no', 'court_name',
                  'usage_status', 'account_status', 'site', 'meter', 'created_by', 'created_at', 'latest_billing','site_id','meter_id']

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
        fields = ['id', 'meter_number', 'meter_type', 'site', 'installed_at', 'status','site_id']


class UnitPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitPrice
        fields = ['id', 'unit_price', 'effective_date']
class BillingRecordSerializer(serializers.ModelSerializer):
    meter = serializers.CharField(source="meter.meter_number", read_only=True)
    meter_id = serializers.UUIDField(source="meter.id", read_only=True)

    class Meta:
        model = BillingRecord
        fields = [
            "id", "customer", "meter", "past_reading", "current_reading", "reading_date",
            "amount_due", "amount_paid", "balance", "unit_price_used",
            "payment_status", "created_by", "created_at", "meter_id"
        ]
        read_only_fields = [
            "amount_due", "unit_price_used", "balance", "payment_status",
            "created_by", "created_at", "meter", "meter_id"
        ]

    def validate(self, data):
        current_reading = data.get("current_reading", self.instance.current_reading if self.instance else None)
        past_reading = data.get("past_reading", self.instance.past_reading if self.instance else None)

        customer = data.get("customer") or (self.instance.customer if self.instance else None)
        if customer and past_reading is None:
            last_billing = customer.billingrecord_set.order_by("-reading_date").first()
            if last_billing:
                past_reading = last_billing.current_reading
                data["past_reading"] = past_reading

        if current_reading is not None and past_reading is not None and current_reading < past_reading:
            raise serializers.ValidationError("Current reading cannot be less than past reading.")

        return data

    @transaction.atomic
    def create(self, validated_data):
        request_user = self.context["request"].user
        validated_data["created_by"] = request_user

        customer = validated_data["customer"]
        meter = customer.meter
        if not meter:
            raise serializers.ValidationError({"customer": "Selected customer has no linked meter."})
        validated_data["meter"] = meter

        existing_billing = customer.billingrecord_set.order_by("-reading_date").first()
        if existing_billing:
            return self.update(existing_billing, validated_data)

        reading_date = validated_data.get("reading_date", timezone.now())
        unit_price = UnitPrice.objects.filter(effective_date__lte=reading_date).order_by("-effective_date").first()
        if not unit_price:
            raise serializers.ValidationError("No unit price available for the given reading date.")

        consumption = validated_data["current_reading"] - validated_data["past_reading"]
        validated_data["amount_due"] = consumption * unit_price.unit_price
        validated_data["unit_price_used"] = unit_price.unit_price
        validated_data["balance"] = validated_data["amount_due"] - validated_data.get("amount_paid", 0)
        validated_data["payment_status"] = (
            "PAID" if validated_data["balance"] <= 0 else
            "PARTIAL" if validated_data.get("amount_paid", 0) > 0 else
            "UNPAID"
        )

        billing = super().create(validated_data)

        ReadingLog.objects.create(
            billing_record=billing,
            previous_reading=billing.past_reading,
            new_reading=billing.current_reading,
            recorded_by=request_user,
            note="Initial reading entry"
        )

        if billing.amount_paid > 0:
            PaymentLog.objects.create(
                billing_record=billing,
                amount_paid=billing.amount_paid,
                payment_method="Manual",
                transaction_reference=f"AUTO-{uuid.uuid4().hex[:8]}",
                created_by=request_user
            )

        return billing

    @transaction.atomic
    def update(self, instance, validated_data):
        request_user = self.context["request"].user

        # Preserve old values
        old_current_reading = instance.current_reading
        old_amount_due = instance.amount_due
        old_amount_paid = instance.amount_paid

        customer = validated_data.get("customer", instance.customer)
        meter = customer.meter
        if not meter:
            raise serializers.ValidationError({"customer": "Selected customer has no linked meter."})
        validated_data["meter"] = meter

        new_current_reading = validated_data.get("current_reading", old_current_reading)

        # Ensure reading never decreases
        if new_current_reading < old_current_reading:
            raise serializers.ValidationError({"current_reading": "Current reading cannot be less than existing reading."})

        # Set past reading to old current reading
        validated_data["past_reading"] = old_current_reading

        reading_date = validated_data.get("reading_date", instance.reading_date)
        unit_price = UnitPrice.objects.filter(effective_date__lte=reading_date).order_by("-effective_date").first()
        if not unit_price:
            raise serializers.ValidationError("No unit price available for the given reading date.")

        # Calculate new consumption increment
        consumption_increment = new_current_reading - old_current_reading
        new_amount_due_increment = consumption_increment * unit_price.unit_price

        validated_data["amount_due"] = old_amount_due + new_amount_due_increment
        validated_data["unit_price_used"] = unit_price.unit_price

        updated_billing = super().update(instance, validated_data)

        # Log reading change
        if new_current_reading != old_current_reading:
            ReadingLog.objects.create(
                billing_record=updated_billing,
                previous_reading=old_current_reading,
                new_reading=new_current_reading,
                recorded_by=request_user,
                note="Reading updated"
            )

        # Handle payments incrementally
        payment_delta = updated_billing.amount_paid - old_amount_paid
        if payment_delta > 0:
            PaymentLog.objects.create(
                billing_record=updated_billing,
                amount_paid=payment_delta,
                payment_method="Manual",
                transaction_reference=f"AUTO-{uuid.uuid4().hex[:8]}",
                created_by=request_user
            )

        updated_billing.balance = updated_billing.amount_due - updated_billing.amount_paid
        updated_billing.payment_status = (
            "PAID" if updated_billing.balance <= 0 else
            "PARTIAL" if updated_billing.amount_paid > 0 else
            "UNPAID"
        )
        updated_billing.save()

        return updated_billing



class PaymentLogSerializer(serializers.ModelSerializer):
    billing_record_id = serializers.UUIDField(source='billing_record.id', read_only=True)
    customer = serializers.CharField(source='billing_record.customer.first_name', read_only=True)

    class Meta:
        model = PaymentLog
        fields = [
            'id', 'billing_record_id', 'customer',
            'amount_paid', 'payment_method', 'transaction_reference',
            'payment_date', 'created_by', 'created_at'
        ]
        read_only_fields = fields  


class ReadingLogSerializer(serializers.ModelSerializer):
    billing_record_id = serializers.UUIDField(source='billing_record.id', read_only=True)
    customer = serializers.CharField(source='billing_record.customer.first_name', read_only=True)
    meter = serializers.CharField(source='billing_record.meter.meter_number', read_only=True)

    class Meta:
        model = ReadingLog
        fields = [
            'id', 'billing_record_id', 'customer', 'meter',
            'previous_reading', 'new_reading', 'recorded_by', 'recorded_at', 'note'
        ]
        read_only_fields = fields  
