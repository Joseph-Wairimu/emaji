# Wait for postgres
echo "Waiting for postgres..."
while ! nc -z db 5432; do
  sleep 0.1
done
echo "PostgreSQL started"

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Create admin user
python manage.py create_admin

# Start server
exec gunicorn billing_project.wsgi:application --bind 0.0.0.0:8000 --workers 3