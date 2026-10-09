import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIELDS = [
    "Location",
    "Lens",
    "Severity",
    "Evidence",
    "Ruby counterpart",
    "Proposed fix",
    "Behavior change",
]
LENSES = [
    "Logic",
    "Concurrency and fork",
    "Host safety",
    "Contract and integrator experience",
    "Adapters",
    "Architecture",
    "Code quality",
    "Performance",
    "Security",
    "Tests",
    "Packaging and docs",
]
SEVERITIES = ["defect", "risk", "test gap", "design", "cleanup", "docs"]
VERDICTS = ["applies", "already correct", "not applicable"]
FINDINGS = ["findings.md", "findings-risks.md", "findings-design.md"]
HEADING = re.compile(r"^### (P-[A-Za-z0-9-]+): (.+)$", re.M)


def findings(directory):
    result = []
    for name in FINDINGS:
        text = (directory / name).read_text()
        matches = list(HEADING.finditer(text))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[match.end() : end]
            body = body.split("\n## ")[0]
            result.append((match.group(1), match.group(2), body, name))
    return result


def check_findings(directory, errors):
    items = findings(directory)
    last = 0
    for identifier, _title, body, name in items:
        labels = re.findall(r"^- \*\*([^*]+):\*\* (.*)$", body, re.M)
        names = [label for label, _ in labels]
        if names != FIELDS:
            errors.append(f"{name} {identifier}: fields are {names}")
            continue
        values = dict(labels)
        if any(not value.strip() for value in values.values()):
            errors.append(f"{name} {identifier}: an empty field")
        if values["Lens"] not in LENSES:
            errors.append(f"{name} {identifier}: unknown lens {values['Lens']!r}")
        if values["Severity"] not in SEVERITIES:
            errors.append(
                f"{name} {identifier}: unknown severity {values['Severity']!r}"
            )
            continue
        rank = SEVERITIES.index(values["Severity"])
        if rank < last:
            errors.append(f"{name} {identifier}: out of severity order")
        last = rank
        if not re.match(r"(yes|no|only|a |fewer)", values["Behavior change"]):
            errors.append(
                f"{name} {identifier}: behavior change does not start with yes or no"
            )
    identifiers = [identifier for identifier, *_ in items]
    if len(set(identifiers)) != len(identifiers):
        errors.append("duplicate finding identifiers")
    numbered = [i for i in identifiers if re.fullmatch(r"P-\d+", i)]
    if numbered and numbered != [f"P-{n:02d}" for n in range(1, len(identifiers) + 1)]:
        errors.append("finding numbers are not 01 to N in order")
    return items


def check_map(directory, errors):
    text = (directory / "map.md").read_text()
    mapped = set(re.findall(r"^- \*\*`(hirefire_resource/[^`]+)`:\*\*", text, re.M))
    found = {
        str(path.relative_to(ROOT))
        for path in (ROOT / "hirefire_resource").rglob("*.py")
    }
    if mapped != found:
        errors.append(f"map differs from the source tree: {sorted(mapped ^ found)}")
    return len(mapped), len(found)


def check_comparison(directory, identifiers, errors):
    text = (directory / "ruby-comparison.md").read_text()
    section = text.split("## Results")[1].split("\n## ")[0]
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.startswith("|") and not re.match(r"^\|\s*(-|Ruby result)", line)
    ]
    expected = (
        [f"F-{n:02d}" for n in range(1, 61)]
        + [f"C-{n:02d}" for n in range(1, 7)]
        + ["`fork_safe` mark", "prefork wait"]
    )
    names = [row[0] for row in rows]
    if names != expected:
        errors.append(
            f"comparison rows: {len(rows)}, expected the 68 Ruby results in order"
        )
    counts = {verdict: 0 for verdict in VERDICTS}
    for row in rows:
        if len(row) < 5:
            errors.append(f"comparison {row[0]}: {len(row)} cells")
            continue
        verdict, evidence = row[2], row[3]
        if verdict not in VERDICTS:
            errors.append(f"comparison {row[0]}: verdict {verdict!r}")
            continue
        counts[verdict] += 1
        if not evidence:
            errors.append(f"comparison {row[0]}: no evidence")
        if verdict == "applies":
            named = re.findall(r"P-[A-Za-z0-9-]+", evidence)
            if not named or named[0] not in identifiers:
                errors.append(f"comparison {row[0]}: applies without a known finding")
            if row[4] not in ("reading", "comparison", "stress"):
                errors.append(f"comparison {row[0]}: found by {row[4]!r}")
    return len(rows), counts


def check_lenses(directory, errors):
    text = (directory / "lenses.md").read_text()
    sections = re.findall(r"^## (.+)$", text, re.M)
    if sections != LENSES:
        errors.append(f"lens sections are {sections}")
    return len(sections)


def main():
    directory = Path(sys.argv[1]).resolve()
    errors = []
    items = check_findings(directory, errors)
    mapped, found = check_map(directory, errors)
    rows, counts = check_comparison(directory, {i for i, *_ in items}, errors)
    lenses = check_lenses(directory, errors)
    references = set()
    for path in directory.glob("*.md"):
        references |= set(re.findall(r"P-(?:\d+|[a-z][a-z0-9-]+)", path.read_text()))
    unknown = sorted(references - {i for i, *_ in items})
    if unknown:
        errors.append(f"references to findings that do not exist: {unknown}")
    command = "find hirefire_resource -name '*.py' | wc -l"
    count = subprocess.run(
        command, shell=True, cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    severities = {}
    for _identifier, _title, body, _name in items:
        match = re.search(r"\*\*Severity:\*\* (.+)", body)
        if match:
            severities[match.group(1)] = severities.get(match.group(1), 0) + 1
    alone = [
        identifier
        for identifier, _title, body, _name in items
        if re.search(r"\*\*Ruby counterpart:\*\* none", body)
    ]
    print(
        f"findings: {len(items)} with all {len(FIELDS)} fields in order ({severities})"
    )
    print(f"without a Ruby counterpart: {len(alone)} ({', '.join(alone)})")
    print(f"map: {mapped} files, find counts {count}")
    print(f"comparison: {rows} rows ({counts})")
    print(f"lenses: {lenses} sections")
    for error in errors:
        print(f"ERROR {error}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
