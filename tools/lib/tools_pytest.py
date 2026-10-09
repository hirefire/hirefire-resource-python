import json
import os
import random


def pytest_collection_modifyitems(session, config, items):
    seed = os.environ.get("TOOLS_SEED")
    if not seed:
        return
    rng = random.Random(seed)
    modules = {}
    for item in items:
        module = item.nodeid.split("::", 1)[0]
        scope = item.nodeid.rsplit("::", 1)[0]
        modules.setdefault(module, {}).setdefault(scope, []).append(item)
    ordered = []
    module_names = sorted(modules)
    rng.shuffle(module_names)
    for module in module_names:
        scopes = sorted(modules[module])
        rng.shuffle(scopes)
        for scope in scopes:
            group = modules[module][scope]
            rng.shuffle(group)
            ordered.extend(group)
    items[:] = ordered


def pytest_runtest_logreport(report):
    path = os.environ.get("TOOLS_RECORD")
    if not path:
        return
    if report.when != "call" and report.outcome == "passed":
        return
    record = {
        "id": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "seconds": round(report.duration, 4),
    }
    with open(path, "a") as file:
        file.write(json.dumps(record) + "\n")


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("TOOLS_RECORD")
    if not path:
        return
    with open(path, "a") as file:
        file.write(json.dumps({"session": int(exitstatus)}) + "\n")
