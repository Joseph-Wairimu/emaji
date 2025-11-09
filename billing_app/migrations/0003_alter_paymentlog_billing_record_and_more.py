import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing_app', '0002_remove_paymentlog_customer_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentlog',
            name='billing_record',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='billing_app.billingrecord'),
        ),
        migrations.AlterField(
            model_name='readinglog',
            name='billing_record',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='readings', to='billing_app.billingrecord'),
        ),
    ]