from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from decouple import config
import sys

User = get_user_model()


class Command(BaseCommand):
    help = 'Create admin user from environment variables'

    def handle(self, *args, **options):
        admin_email = config('ADMIN_EMAIL', default=None)
        admin_password = config('ADMIN_PASSWORD', default=None)
        
        if not admin_email or not admin_password:
            self.stdout.write(
                self.style.WARNING('ADMIN_EMAIL and ADMIN_PASSWORD must be set in environment')
            )
            return
        
        if User.objects.filter(email=admin_email).exists():
            self.stdout.write(
                self.style.WARNING(f'Admin user with email {admin_email} already exists')
            )
            return
        
        try:
            admin_user = User.objects.create_superuser(
                email=admin_email,
                password=admin_password,
                username='admin',
                first_name='Admin',
                last_name='User'
            )
            self.stdout.write(
                self.style.SUCCESS(f'Successfully created admin user: {admin_user.email}')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to create admin user: {str(e)}')
            )
            sys.exit(1)