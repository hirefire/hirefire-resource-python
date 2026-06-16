"""Guard: every middleware/ and macro/ test must be named by a tox env."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = ROOT / "tests" / "hirefire_resource"


def test_every_integration_test_is_wired_into_a_tox_env():
    tox = (ROOT / "tox.ini").read_text()
    test_files = sorted(INTEGRATION_DIR.glob("middleware/test_*.py"))
    test_files += sorted(INTEGRATION_DIR.glob("macro/test_*.py"))
    orphans = [
        path.relative_to(ROOT).as_posix()
        for path in test_files
        if path.relative_to(ROOT).as_posix() not in tox
    ]
    assert not orphans, f"tox.ini names no env for: {orphans}"
