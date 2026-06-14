import os
import re

# Resolves the name of the process this code is running in, so collectors can
# tell whether they should report under a given declared dyno name. First
# non-empty source wins; None means unresolved.


def resolve():
    return explicit() or heroku_dyno() or render_service()


def explicit():
    return presence(os.environ.get("HIREFIRE_SERVICE_NAME"))


# Heroku sets DYNO per generation: Cedar uses "web.1" (process type before the
# first "."); Fir uses Kubernetes pod names like "web-5fb9c979-lft2l". Stripping
# the two trailing "-<alnum>" segments, rather than splitting on the first "-",
# keeps any dash inside a process name intact.
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


# Heroku config vars are app-wide, so a dashboard-set HIREFIRE_SERVICE_NAME makes
# every dyno identify as the same name. True when an explicit name disagrees with
# the DYNO prefix. Case-insensitive, matching the identity gates: names differing
# only in case gate identically.
def heroku_conflict():
    explicit_name = explicit()
    dyno_name = heroku_dyno()
    return bool(
        explicit_name and dyno_name and explicit_name.lower() != dyno_name.lower()
    )


def presence(value):
    return value if value else None
