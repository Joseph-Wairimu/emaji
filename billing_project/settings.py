import os
from decouple import config
from pathlib import Path
from decouple import config
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='fverevefvegfvgef')
DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'drf_spectacular',
    'billing_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'billing_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'billing_project.wsgi.application'
# print("Full Environment Variables:")
# for key, value in os.environ.items():
#     print(f"{key}: {value}")
# print("Resolved Database Config:")
# print({
#     "NAME": config("POSTGRES_DB"),
#     "USER": config("POSTGRES_USER"),
#     "PASSWORD": config("POSTGRES_PASSWORD"),
#     "HOST": config("POSTGRES_HOST", default="localhost"),
#     "PORT": config("POSTGRES_PORT", default="5432")
# })
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB"),
        "USER": config("POSTGRES_USER"),
        "PASSWORD": config("POSTGRES_PASSWORD"),
        "HOST": config("POSTGRES_HOST", default="localhost"),
        "PORT": config("POSTGRES_PORT", default="5432"),
    }
}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}



SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=24), 
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),  
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Meter Billing API',
    'DESCRIPTION': 'API for managing sites, customers, meters, and billing.',
    'VERSION': '1.0.0',
}

AUTH_USER_MODEL = 'billing_app.User'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Nairobi'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} — {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'billing_app': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# Smart meter / Fengbo Cloud integration
FENGBO_API_URL = config('FENGBO_API_URL', default='')
FENGBO_AREA_NAME = config('FENGBO_AREA_NAME', default='')
FENGBO_CHECK_CODE = config('FENGBO_CHECK_CODE', default='')
SMART_METER_API_KEY = config('SMART_METER_API_KEY', default='')


# Card-swipe terminal integration
CARD_TERMINAL_API_KEY = config('CARD_TERMINAL_API_KEY', default='')

# Nuomiy / IDMP merchant API
NUOMIY_BASE_URL = config('NUOMIY_BASE_URL', default='https://ivmer.nuomiy.com/meropen')
NUOMIY_APP_ID = config('NUOMIY_APP_ID', default='')
NUOMIY_PRIVATE_KEY = config('NUOMIY_PRIVATE_KEY', default='')
# Platform-specific IDs — confirm with /basesetting/cardtypes, /depts, /transactions
NUOMIY_DEFAULT_CARD_TYPE = config('NUOMIY_DEFAULT_CARD_TYPE', default='1')
NUOMIY_DEFAULT_DEPT_ID = config('NUOMIY_DEFAULT_DEPT_ID', default='1')
NUOMIY_DEFAULT_WALLET_TYPE = config('NUOMIY_DEFAULT_WALLET_TYPE', default='1')
NUOMIY_DEFAULT_TRANSACTION_TYPE = config('NUOMIY_DEFAULT_TRANSACTION_TYPE', default='1')