import importlib
import inspect
import json
import re
import sys


def text(value):
    return re.sub(r" at 0x[0-9a-f]+", "", repr(value))[:120]


def signature(value):
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return None


def describe_class(cls):
    members = {}
    for name, value in vars(cls).items():
        if name.startswith("_") and name != "__init__":
            continue
        raw = value
        kind = "attribute"
        if isinstance(raw, (classmethod, staticmethod)):
            kind = type(raw).__name__
            raw = raw.__func__
            while hasattr(raw, "__wrapped__") and not inspect.isfunction(raw):
                raw = raw.__wrapped__
        if isinstance(raw, property):
            kind = "property" if raw.fset is None else "property (settable)"
            members[name] = {"kind": kind, "signature": signature(raw.fget)}
        elif inspect.isfunction(raw):
            kind = "method" if kind == "attribute" else kind
            if inspect.iscoroutinefunction(raw):
                kind = "async " + kind
            members[name] = {"kind": kind, "signature": signature(raw)}
        elif inspect.isclass(raw):
            members[name] = {"kind": "class", "signature": signature(raw)}
        else:
            members[name] = {"kind": kind, "value": text(raw)}
    bases = [base.__name__ for base in cls.__bases__ if base is not object]
    return {"kind": "class", "bases": bases, "members": members}


def describe_module(module_name):
    module = importlib.import_module(module_name)
    names = {}
    for name, value in vars(module).items():
        if name.startswith("_"):
            continue
        if inspect.ismodule(value):
            continue
        origin = getattr(value, "__module__", None)
        if inspect.isclass(value):
            if origin != module_name:
                names[name] = {"kind": "import", "from": origin}
            else:
                names[name] = describe_class(value)
        elif inspect.isfunction(value):
            if origin != module_name:
                names[name] = {"kind": "import", "from": origin}
            else:
                kind = (
                    "async function"
                    if inspect.iscoroutinefunction(value)
                    else "function"
                )
                names[name] = {"kind": kind, "signature": signature(value)}
        elif (
            origin is not None
            and not origin.startswith("hirefire_resource")
            and origin not in ("builtins",)
        ):
            names[name] = {"kind": "import", "from": origin}
        else:
            names[name] = {"kind": "constant", "value": text(value)}
    result = {"names": names}
    if hasattr(module, "__all__"):
        result["all"] = list(module.__all__)
    return result


if __name__ == "__main__":
    output = {}
    for module_name in sys.argv[1:]:
        try:
            output[module_name] = describe_module(module_name)
        except Exception as error:
            output[module_name] = {"error": f"{type(error).__name__}: {error}"}
    json.dump(output, sys.stdout)
