from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('billing_app', '0013_add_nuomiy_card_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='MpesaTopupRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('amount_kes', models.DecimalField(decimal_places=2, max_digits=12)),
                ('phone_number', models.CharField(max_length=20)),
                ('checkout_request_id', models.CharField(db_index=True, max_length=100, unique=True)),
                ('merchant_request_id', models.CharField(blank=True, max_length=100)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('success', 'Success'),
                        ('failed', 'Failed'),
                        ('cancelled', 'Cancelled'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('result_code', models.CharField(blank=True, max_length=10)),
                ('result_desc', models.TextField(blank=True)),
                ('mpesa_receipt_number', models.CharField(blank=True, max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('customer', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='mpesa_topups',
                    to='billing_app.customer',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
