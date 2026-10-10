import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common

STATUSES = ["open", "fixed", "closed", "blocked"]
TOTAL = 71


def git(*arguments):
    return subprocess.run(
        ["git", *arguments], cwd=common.ROOT, capture_output=True, text=True
    )


def message(commit):
    result = git("log", "-1", "--format=%B", commit)
    return result.stdout if result.returncode == 0 else None


def rows(plan):
    for line in Path(plan).read_text().splitlines():
        if not re.match(r"\| P-\d\d ", line):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        yield dict(zip(["id", "subject", "step", "status", "commit", "note"], cells))


def own_commits(cell):
    return [
        match.group(2)
        for match in re.finditer(r"((?:canon|ruby) )?`([0-9a-f]{7,40})`", cell)
        if not match.group(1)
    ]


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: tools/bin/check-fixes FIXES_MD_OF_THE_CANON [BASE_BRANCH]")
    plan = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else "python-audit"

    problems = []
    table = list(rows(plan))
    if len(table) != TOTAL:
        problems.append(f"the status table has {len(table)} rows, expected {TOTAL}")

    listed = {}
    for index, row in enumerate(table, start=1):
        expected = f"P-{index:02d}"
        if row["id"] != expected:
            problems.append(f"{row['id']}: expected {expected} here")
        if row["status"] not in STATUSES:
            problems.append(f"{row['id']}: unknown status {row['status']!r}")
        if row["status"] == "open":
            problems.append(f"{row['id']}: open")
        if row["status"] in ("closed", "blocked") and not row["note"]:
            problems.append(f"{row['id']}: {row['status']} without a note")
        commits = own_commits(row["commit"])
        if row["status"] == "fixed" and not re.search(
            r"`[0-9a-f]{7,40}`", row["commit"]
        ):
            problems.append(f"{row['id']}: fixed without a commit")
        for commit in commits:
            body = message(commit)
            if body is None:
                problems.append(f"{row['id']}: commit {commit} does not exist")
            elif row["id"] not in body:
                problems.append(
                    f"{row['id']}: commit {commit} does not name the finding"
                )
        listed[row["id"]] = commits

    log = git("log", "--format=%h%x00%B%x01", f"{base}..HEAD").stdout
    for entry in log.split("\x01"):
        commit, _, body = entry.strip().partition("\x00")
        named = re.search(r"^Audit findings? (.+)\.$", body, re.MULTILINE)
        if not named:
            continue
        for finding in re.findall(r"P-\d\d", named.group(1)):
            commits = listed.get(finding, [])
            if not any(commit.startswith(known[:7]) for known in commits):
                problems.append(
                    f"{finding}: commit {commit} names it and the table does not"
                )

    counts = {status: 0 for status in STATUSES}
    for row in table:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(
        f"findings: {len(table)}, "
        + ", ".join(f"{status} {counts.get(status, 0)}" for status in STATUSES)
    )
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print(f"{len(problems)} problems" if problems else "no problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
