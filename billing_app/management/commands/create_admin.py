from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from decouple import config

User = get_user_model()


class Command(BaseCommand):
    help = 'Create admin user from environment variables'

    def handle(self, *args, **options):
        from billing_app.models import Role

        admin_email = config('ADMIN_EMAIL', default=None)
        admin_password = config('ADMIN_PASSWORD', default=None)

        if not admin_email or not admin_password:
            self.stdout.write(
                self.style.WARNING('ADMIN_EMAIL and ADMIN_PASSWORD must be set in environment')
            )
            return

        admin_role, _ = Role.objects.get_or_create(
            name='SUPER_ADMIN',
            defaults={'description': 'Super administrator with full access'},
        )

        user, created = User.objects.get_or_create(
            email=admin_email,
            defaults={
                'username': 'admin',
                'first_name': 'Admin',
                'last_name': 'User',
                'is_staff': True,
                'is_superuser': True,
            },
        )

        if created:
            user.set_password(admin_password)

        if user.role != admin_role:
            user.role = admin_role
            user.save()
            self.stdout.write(self.style.SUCCESS(
                f'{"Created" if created else "Updated"} admin user {user.email} with SUPER_ADMIN role'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'Admin user {user.email} already exists with SUPER_ADMIN role'
            ))