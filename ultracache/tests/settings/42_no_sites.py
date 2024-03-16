import os


DEBUG = True
TEMPLATE_DEBUG = DEBUG

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
    }
}

ROOT_URLCONF = "ultracache.tests.urls"

INSTALLED_APPS = (
    "test_without_migrations",
    "ultracache",
    # Include tests because it declares models
    "ultracache.tests",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

SECRET_KEY = "SECRET_KEY"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"],
            "loaders": [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
        },
    },
]

MIDDLEWARE = [
    "ultracache.middleware.UltraCacheMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ULTRACACHE = {
    "purge": {"method": "ultracache.tests.utils.dummy_purger"},
    "drf": {"viewsets": {"*": {}}},
    "consider-headers": ["cookie"],
}
