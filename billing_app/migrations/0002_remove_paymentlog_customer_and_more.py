import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing_app', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='paymentlog',
            name='customer',
        ),
        migrations.RemoveField(
            model_name='readinglog',
            name='customer',
        ),
        migrations.RemoveField(
            model_name='readinglog',
            name='meter',
        ),
        migrations.AddField(
            model_name='readinglog',
            name='billing_record',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='readings', to='billing_app.billingrecord'),
        ),
        migrations.AlterField(
            model_name='paymentlog',
            name='billing_record',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='billing_app.billingrecord'),
        ),
    ]