import os
import re


def resolve():
    return explicit() or heroku_dyno() or render_service()


def explicit():
    return presence(os.environ.get("HIREFIRE_SERVICE_NAME"))


def heroku_dyno():
    dyno = presence(os.environ.get("DYNO"))
    if dyno is None:
        return None

    if "." in dyno:
        return dyno.split(".")[0]
    else:
        return re.sub(r"-[a-z0-9]+-[a-z0-9]+\Z", "", dyno)


def render_service():
    return presence(os.environ.get("RENDER_SERVICE_NAME"))


def heroku_conflict():
    explicit_name = explicit()
    dyno_name = heroku_dyno()
    return bool(
        explicit_name and dyno_name and explicit_name.lower() != dyno_name.lower()
    )


def presence(value):
    return value if value else None
