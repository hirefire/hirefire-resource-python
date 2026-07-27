import os
import re


def resolve() -> str | None:
    return explicit() or heroku_dyno() or render_service()


def explicit() -> str | None:
    return presence(os.environ.get("HIREFIRE_SERVICE_NAME"))


def heroku_dyno() -> str | None:
    dyno = presence(os.environ.get("DYNO"))
    if dyno is None:
        return None

    if "." in dyno:
        name = dyno.split(".", 1)[0]
    else:
        name = re.sub(r"-[A-Za-z0-9]+-[A-Za-z0-9]+\Z", "", dyno)
    return presence(name)


def render_service() -> str | None:
    return presence(os.environ.get("RENDER_SERVICE_NAME"))


def heroku_conflict() -> bool:
    explicit_name = explicit()
    dyno_name = heroku_dyno()
    return bool(
        explicit_name and dyno_name and explicit_name.lower() != dyno_name.lower()
    )


def platform_http_role() -> bool:
    return heroku_web_process() or render_web_service()


def heroku_web_process() -> bool:
    name = heroku_dyno()
    return name is not None and name.lower() == "web"


def render_web_service() -> bool:
    service_type = presence(os.environ.get("RENDER_SERVICE_TYPE"))
    return service_type is not None and service_type.lower() == "web"


def presence(value: object | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped else None
