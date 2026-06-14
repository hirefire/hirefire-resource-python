import os
import re

# Resolves this process's name (to match against a declared dyno name). First
# non-empty source wins; None means unresolved.


def resolve():
    return explicit() or heroku_dyno() or render_service()


def explicit():
    return presence(os.environ.get("HIREFIRE_SERVICE_NAME"))


# DYNO is "web.1" on Cedar, a pod name like "web-5fb9c979-lft2l" on Fir. Strip the
# two trailing "-<alnum>" segments, keeping any dash inside the process name.
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


# True when an explicit name disagrees with the DYNO prefix: a dashboard-set
# (app-wide) HIREFIRE_SERVICE_NAME would make every dyno identify the same.
def heroku_conflict():
    explicit_name = explicit()
    dyno_name = heroku_dyno()
    return bool(
        explicit_name and dyno_name and explicit_name.lower() != dyno_name.lower()
    )


def presence(value):
    return value if value else None
