import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common

ENVS = {
    "hirefire_resource.macro.celery": "py314-celery",
    "hirefire_resource.macro.rq": "py314-rq2",
    "hirefire_resource.macro.dramatiq": "py314-dramatiq",
}
DEFAULT_ENV = "py314-core"


def module_name(path):
    parts = Path(path).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def snapshot(root, tox_root):
    modules = [module_name(path) for path in common.source_files(root)]
    by_env = {}
    for module in modules:
        by_env.setdefault(ENVS.get(module, DEFAULT_ENV), []).append(module)
    api = {}
    for env, names in by_env.items():
        python = Path(tox_root) / ".tox" / env / "bin" / "python"
        probe = Path(__file__).resolve().parent / "public_api_probe.py"
        output = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                f"import sys; sys.path.insert(0, {str(root)!r}); sys.argv = ['probe'] + {names!r}; exec(open({str(probe)!r}).read())",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        api.update(json.loads(output))
    return dict(sorted(api.items()))


def render(api):
    lines = []
    for module, entry in api.items():
        if "error" in entry:
            lines.append(f"{module}  ERROR {entry['error']}")
            continue
        if "all" in entry:
            lines.append(f"{module}.__all__ = {entry['all']}")
        for name, item in sorted(entry["names"].items()):
            kind = item["kind"]
            if kind == "import":
                lines.append(f"{module}.{name}  (import from {item['from']})")
            elif kind == "class":
                bases = f"({', '.join(item['bases'])})" if item["bases"] else ""
                lines.append(f"{module}.{name}{bases}  class")
                for member, detail in sorted(item["members"].items()):
                    tail = detail.get("signature") or detail.get("value") or ""
                    lines.append(
                        f"{module}.{name}.{member}{tail if detail.get('signature') else ''}  {detail['kind']}{'' if detail.get('signature') else ' = ' + tail if tail else ''}"
                    )
            elif kind == "constant":
                lines.append(f"{module}.{name} = {item['value']}  constant")
            else:
                lines.append(f"{module}.{name}{item['signature']}  {kind}")
    return "\n".join(lines) + "\n"


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else common.ROOT
    target = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else common.ROOT / "tools" / "baseline"
    )
    api = snapshot(root.resolve(), common.ROOT)
    target.mkdir(parents=True, exist_ok=True)
    (target / "public_api.json").write_text(
        json.dumps(api, indent=2, sort_keys=True) + "\n"
    )
    (target / "public_api.txt").write_text(render(api))
    names = sum(len(entry.get("names", {})) for entry in api.values())
    print(f"{len(api)} modules, {names} public names, written to {target.name}/")


if __name__ == "__main__":
    main()
