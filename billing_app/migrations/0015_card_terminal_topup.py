from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ('billing_app', '0014_mpesa_topup_request'),
    ]

    operations = [
        migrations.CreateModel(
            name='CardTerminalTopup',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('card_no', models.CharField(blank=True, db_index=True, max_length=50)),
                ('amount_kes', models.DecimalField(decimal_places=2, max_digits=12)),
                ('balance_after_kes', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('source', models.CharField(
                    choices=[
                        ('manual', 'Manual / Cash'),
                        ('mpesa_stk', 'M-Pesa STK Push'),
                        ('mpesa_c2b', 'M-Pesa Paybill'),
                    ],
                    max_length=20,
                )),
                ('mpesa_receipt', models.CharField(blank=True, max_length=50)),
                ('phone_number', models.CharField(blank=True, max_length=20)),
                ('reference', models.CharField(max_length=100, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('customer', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='card_terminal_topups',
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
