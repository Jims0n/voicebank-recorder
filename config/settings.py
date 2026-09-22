import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
env = os.environ.get

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = env("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "storages",
    "collector",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": dj_database_url.config(
    default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=600)}

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Lagos"
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

USE_S3 = env("USE_S3", "0") == "1"
STORAGES = {
    "default": {"BACKEND": "storages.backends.s3.S3Storage"} if USE_S3
               else {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if USE_S3:
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL") or None
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", "auto")
    AWS_DEFAULT_ACL = None          # bucket stays private
    AWS_QUERYSTRING_AUTH = True     # signed URLs for admin playback
    AWS_S3_FILE_OVERWRITE = False

DATA_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024

# Collection settings
CONSENT_VERSION = env("CONSENT_VERSION", "v1")
BATCH_SIZE = 40            # prompts per sitting before offering a break
MAX_PER_SPEAKER = 200      # cap so no single voice dominates the dataset
ELICITED_SHARE = 0.3       # share of free-speech prompts vs read prompts
MIN_DURATION_MS = 700
MAX_DURATION_MS = 15000

SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
