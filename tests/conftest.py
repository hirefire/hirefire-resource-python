import os
import sys

from django.conf import settings

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


settings.configure(
    SECRET_KEY="dummy-secret-key",
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    },
)
