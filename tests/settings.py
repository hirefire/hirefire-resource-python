# tests/settings.py

SECRET_KEY = "dummy-secret-key"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # other required apps for your tests
]
MIDDLEWARE = [
    # 'path.to.your.middleware',  # Uncomment and provide the path to your middleware.
]

USE_TZ = True
# any other necessary settings for your middleware or tests
