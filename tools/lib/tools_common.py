import os
import subprocess
from pathlib import Path

ROOT = Path(os.environ.get("TOOLS_ROOT") or Path(__file__).resolve().parents[2])
RESULTS = Path(os.environ.get("TOOLS_RESULTS") or ROOT / "tools" / "results")
VENV = Path(os.environ.get("TOOLS_VENV") or ROOT / "tools" / ".venv")


def results(*parts):
    path = RESULTS.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def tox_envs(root=ROOT):
    output = subprocess.run(
        ["tox", "-l"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return [line.strip() for line in output.splitlines() if line.strip()]


def commit(root=ROOT):
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    ).stdout.strip()


def source_files(root=ROOT):
    return sorted(
        str(path.relative_to(root))
        for path in (Path(root) / "hirefire_resource").rglob("*.py")
    )
