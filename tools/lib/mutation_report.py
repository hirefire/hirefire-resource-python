import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mutation_verdicts
import tools_common as common


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "mutation"
    directory = common.RESULTS / name
    rows = [
        json.loads(line)
        for line in (directory / "mutants.jsonl").read_text().splitlines()
    ]
    files = {}
    groups = {}
    unassigned = []
    survivors = []
    for row in rows:
        entry = files.setdefault(
            row["file"],
            {"changes": 0, "caught": 0, "survived": 0, "unrun": 0, "equivalent": 0},
        )
        entry["changes"] += 1
        status = row["status"]
        if status in ("killed", "timeout"):
            entry["caught"] += 1
            continue
        if status == "error":
            unassigned.append(row)
            continue
        verdict = mutation_verdicts.verdict(row)
        if verdict is None:
            unassigned.append(row)
            continue
        kind, group, reason = verdict
        if kind == "equivalent":
            entry["equivalent"] += 1
        elif status == "uncovered":
            entry["unrun"] += 1
        else:
            entry["survived"] += 1
        bucket = groups.setdefault(
            group, {"kind": kind, "reason": reason, "changes": 0, "files": {}}
        )
        bucket["changes"] += 1
        bucket["files"][row["file"]] = bucket["files"].get(row["file"], 0) + 1
        survivors.append({**row, "group": group, "kind": kind, "reason": reason})

    for entry in files.values():
        entry["score"] = (
            round(100.0 * (entry["caught"] + entry["equivalent"]) / entry["changes"], 1)
            if entry["changes"]
            else 100.0
        )
    total = {
        key: sum(entry[key] for entry in files.values())
        for key in ("changes", "caught", "survived", "unrun", "equivalent")
    }
    total["score"] = round(
        100.0 * (total["caught"] + total["equivalent"]) / total["changes"], 1
    )
    document = {"total": total, "files": files, "groups": groups}
    (directory / "scores.json").write_text(json.dumps(document, indent=2) + "\n")
    with open(directory / "survivors.tsv", "w") as file:
        file.write("group\tkind\tstatus\tfile\tline\toperator\tdescription\treason\n")
        for row in sorted(survivors, key=lambda r: (r["group"], r["file"], r["line"])):
            file.write(
                f"{row['group']}\t{row['kind']}\t{row['status']}\t{row['file']}\t{row['line']}\t"
                f"{row['operator']}\t{row['description']}\t{row['reason']}\n"
            )

    print(
        f"{'file under hirefire_resource/':<32} {'changes':>7} {'caught':>6} {'equiv':>5} {'surv':>5} {'unrun':>5} {'score':>6}"
    )
    for name, entry in sorted(files.items()):
        print(
            f"{name.removeprefix('hirefire_resource/'):<32} {entry['changes']:>7} {entry['caught']:>6} {entry['equivalent']:>5} "
            f"{entry['survived']:>5} {entry['unrun']:>5} {entry['score']:>5}%"
        )
    print(
        f"{'TOTAL':<32} {total['changes']:>7} {total['caught']:>6} {total['equivalent']:>5} "
        f"{total['survived']:>5} {total['unrun']:>5} {total['score']:>5}%"
    )
    for group, bucket in sorted(groups.items()):
        reason = (
            "see the reason per change" if group == "equivalent" else bucket["reason"]
        )
        print(
            f"group {group:<22} {bucket['kind']:<10} {bucket['changes']:>4}  {reason}"
        )
    controls = directory / "controls.jsonl"
    if controls.exists():
        entries = [json.loads(line) for line in controls.read_text().splitlines()]
        failed = sum(1 for entry in entries if entry["code"] != 0)
        print(
            f"test sets run on the unchanged tree before the changes: {len(entries)}, failed: {failed}"
        )
    unrepeated = sum(1 for row in rows if row.get("kills_not_repeated"))
    print(
        f"changes with a kill that did not repeat, counted as not caught: {unrepeated}"
    )
    for row in unassigned:
        print(
            f"NO VERDICT {row['status']} {row['file']}:{row['line']} {row['operator']} {row['description']}"
        )
    sys.exit(1 if unassigned else 0)


if __name__ == "__main__":
    main()
