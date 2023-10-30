import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from django.conf import settings
    import django

    if not settings.configured:
        settings.configure(
            SECRET_KEY="dummy-secret-key",
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
        )
        django.setup()
except ImportError:
    pass
