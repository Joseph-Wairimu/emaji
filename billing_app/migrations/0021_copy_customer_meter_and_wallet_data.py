from django.db import migrations


def forwards(apps, schema_editor):
    Customer = apps.get_model('billing_app', 'Customer')
    PrepaidWallet = apps.get_model('billing_app', 'PrepaidWallet')
    SmartMeterWallet = apps.get_model('billing_app', 'SmartMeterWallet')

    for customer in Customer.objects.exclude(meter__isnull=True):
        meter = customer.meter
        meter.customer = customer
        meter.save(update_fields=['customer'])

    for wallet in PrepaidWallet.objects.all():
        customer = wallet.customer
        meter = customer.meter
        if not meter:
            continue
        SmartMeterWallet.objects.update_or_create(
            meter=meter,
            defaults={
                'customer': customer,
                'balance_m3': wallet.balance_m3,
                'last_known_flow_m3': wallet.last_known_flow_m3,
                'valve_status': wallet.valve_status,
            },
        )


def backwards(apps, schema_editor):
    Meter = apps.get_model('billing_app', 'Meter')
    SmartMeterWallet = apps.get_model('billing_app', 'SmartMeterWallet')
    PrepaidWallet = apps.get_model('billing_app', 'PrepaidWallet')

    for wallet in SmartMeterWallet.objects.all():
        pw, _ = PrepaidWallet.objects.get_or_create(customer=wallet.customer)
        pw.balance_m3 = wallet.balance_m3
        pw.last_known_flow_m3 = wallet.last_known_flow_m3
        pw.valve_status = wallet.valve_status
        pw.save(update_fields=['balance_m3', 'last_known_flow_m3', 'valve_status'])

    for meter in Meter.objects.exclude(customer__isnull=True):
        customer = meter.customer
        customer.meter = meter
        customer.save(update_fields=['meter'])


class Migration(migrations.Migration):

    dependencies = [
        ('billing_app', '0020_add_meter_customer_and_smart_meter_wallet'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
