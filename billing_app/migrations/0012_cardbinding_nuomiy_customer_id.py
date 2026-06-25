from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing_app', '0011_customer_user_link'),
    ]

    operations = [
        migrations.AddField(
            model_name='cardbinding',
            name='nuomiy_customer_id',
            field=models.CharField(
                max_length=50,
                blank=True,
                null=True,
                help_text='Customer ID assigned by the Nuomiy cloud platform on registration',
            ),
        ),
    ]
