import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common

FRAMEWORKS = [
    "django4",
    "django5",
    "django6",
    "flask2",
    "flask3",
    "quart",
    "fastapi",
    "starlette",
    "starlette1",
]
LIBRARIES = {"celery": ["celery"], "rq": ["rq", "rq2"], "dramatiq": ["dramatiq"]}


def changed_paths():
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=common.ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line[3:].split(" -> ")[-1] for line in status.splitlines()]


def factors(path):
    if not path.startswith(("hirefire_resource/", "tests/")):
        return []
    parts = Path(path).parts
    if "macro" in parts:
        for library, names in LIBRARIES.items():
            if library in Path(path).name:
                return names
    if "middleware" in parts and parts[0] == "tests":
        return FRAMEWORKS
    return None


def select(paths, envs):
    wanted = set()
    for path in paths:
        names = factors(path)
        if names is None:
            return envs
        wanted.update(names)
    return [env for env in envs if env.split("-", 1)[1] in wanted]


def main():
    everything = "--all" in sys.argv[1:]
    check = subprocess.run(["poetry", "run", "paver", "check"], cwd=common.ROOT)
    if check.returncode != 0:
        sys.exit("gate: paver check failed")

    envs = common.tox_envs()
    selected = envs if everything else select(changed_paths(), envs)
    print(f"gate: {len(selected)} of {len(envs)} envs", flush=True)
    if not selected:
        return
    run = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "matrix_run.py"),
            "gate",
            "--only",
            ",".join(selected),
        ],
        cwd=common.ROOT,
    )
    sys.exit(run.returncode)


if __name__ == "__main__":
    main()
