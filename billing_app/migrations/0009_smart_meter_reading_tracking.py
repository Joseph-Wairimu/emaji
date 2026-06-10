import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing_app', '0008_valvecommand_acknowledged_at_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── PaymentLog changes ────────────────────────────────────────────
        # Make billing_record nullable (prepaid top-ups have no billing record)
        migrations.AlterField(
            model_name='paymentlog',
            name='billing_record',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='payments',
                to='billing_app.billingrecord',
            ),
        ),
        # Direct customer link for prepaid payments
        migrations.AddField(
            model_name='paymentlog',
            name='customer',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='payment_logs',
                to='billing_app.customer',
            ),
        ),
        # Distinguish prepaid vs postpaid payments
        migrations.AddField(
            model_name='paymentlog',
            name='billing_type',
            field=models.CharField(
                choices=[('POSTPAID', 'Postpaid'), ('PREPAID', 'Prepaid')],
                default='POSTPAID',
                max_length=20,
            ),
        ),

        # ── ReadingLog changes ────────────────────────────────────────────
        # Make billing_record nullable (smart meter snapshots have no billing record)
        migrations.AlterField(
            model_name='readinglog',
            name='billing_record',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='readings',
                to='billing_app.billingrecord',
            ),
        ),
        # Direct meter link for smart meter snapshot readings
        migrations.AddField(
            model_name='readinglog',
            name='meter',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reading_logs',
                to='billing_app.meter',
            ),
        ),
        # Direct customer link for smart meter snapshot readings
        migrations.AddField(
            model_name='readinglog',
            name='customer',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reading_logs',
                to='billing_app.customer',
            ),
        ),
        # Distinguish prepaid vs postpaid readings
        migrations.AddField(
            model_name='readinglog',
            name='billing_type',
            field=models.CharField(
                choices=[('POSTPAID', 'Postpaid'), ('PREPAID', 'Prepaid')],
                default='POSTPAID',
                max_length=20,
            ),
        ),
    ]
