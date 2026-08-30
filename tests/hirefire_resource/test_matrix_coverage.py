import re
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


def test_readme_runtime_floor_matches_requires_python():
    pyproject = (ROOT / "pyproject.toml").read_text()
    requires = re.search(r'^requires-python\s*=\s*"([^"]+)"', pyproject, re.M)
    assert requires is not None
    floor = re.search(r"(\d+\.\d+)", requires.group(1)).group(1)
    assert _section("Supported runtimes") == [f"Python {floor}+"]


def test_every_tox_integration_factor_is_in_the_readme_and_the_readme_has_no_extras():
    cells = _tox_cells()
    floors: dict[str, set[int]] = {}
    py_by_factor_major: dict[tuple[str, int], set[int]] = {}
    all_pys: set[int] = set()
    for factor, major, pys in cells:
        if factor == "core":
            all_pys.update(pys)
            continue
        floors.setdefault(factor, set()).add(major)
        py_by_factor_major.setdefault((factor, major), set()).update(pys)
        all_pys.update(pys)

    bullets = _section("Supported web frameworks") + _section(
        "Supported worker libraries"
    )
    used: list[str] = []
    for factor, majors in floors.items():
        bullet = next((line for line in bullets if _matches_factor(factor, line)), None)
        assert bullet, f"README missing a line for tox factor {factor} ({min(majors)}+)"
        used.append(bullet)
        assert f"{min(majors)}+" in bullet
        assert "size only" not in bullet
    assert sorted(set(used)) == sorted(bullets)

    global_py = min(all_pys)
    readme = (ROOT / "README.md").read_text()
    for (factor, major), pys in py_by_factor_major.items():
        factor_py = min(pys)
        if factor_py <= global_py:
            continue
        bullet = next(line for line in bullets if _matches_factor(factor, line))
        label = re.match(r"(.+?)\s+\d", bullet).group(1)
        py_label = f"{factor_py // 100}.{factor_py % 100}"
        assert f"{label} {major} requires Python {py_label}+" in readme


def _section(heading: str) -> list[str]:
    readme = (ROOT / "README.md").read_text()
    match = re.search(
        rf"\*\*{re.escape(heading)}:\*\*\n\n((?:- .+\n)+)",
        readme,
    )
    assert match, f"missing README section {heading}"
    return [
        line[2:].strip()
        for line in match.group(1).splitlines()
        if line.startswith("- ")
    ]


def _tox_cells() -> list[tuple[str, int, set[int]]]:
    tox = (ROOT / "tox.ini").read_text()
    cells = []
    for heading, body in re.findall(
        r"\[testenv:([^\]]+)\]\n(.*?)(?=\n\[|\Z)", tox, re.S
    ):
        py_part, _, rest = heading.partition("-")
        pys = {int(n) for n in re.findall(r"\d+", py_part)}
        factor = re.sub(r"\d+$", "", rest)
        if factor == "core":
            cells.append((factor, 0, pys))
            continue
        dep = re.search(rf"^\s*{re.escape(factor)}(?:\[[^\]]+\])?([^\n]+)", body, re.M)
        assert dep, f"no dep pin for {factor} in {heading}"
        pin = dep.group(1)
        if re.search(r"[<]=?\s*1\b", pin) and "~=" not in pin:
            major = 0
        else:
            major_match = re.search(r"(\d+)", pin)
            assert major_match, pin
            major = int(major_match.group(1))
        cells.append((factor, major, pys))
    return cells


def _matches_factor(factor: str, line: str) -> bool:
    return _bullet_token(line) == _normalize(factor)


def _bullet_token(line: str) -> str:
    stem = re.sub(r"\s+\(.*\)\s*$", "", line)
    label = re.match(r"(.+?)\s+\d", stem)
    return _normalize(label.group(1) if label else stem)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
