import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coverage
import tools_common as common


def main():
    run = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    directory = common.results(run)
    data_files = sorted((directory / "coverage").glob("*.coverage"))
    if not data_files:
        sys.exit(f"no coverage data under {directory / 'coverage'}")

    merged = directory / "merged.coverage"
    merged.unlink(missing_ok=True)
    config = str(common.ROOT / "tools" / "coverage.ini")
    combined = coverage.Coverage(data_file=str(merged), config_file=config)
    combined.combine([str(path) for path in data_files], keep=True)
    combined.save()

    report = directory / "coverage-raw.json"
    cov = coverage.Coverage(data_file=str(merged), config_file=config)
    cov.load()
    cov.json_report(outfile=str(report), include=["hirefire_resource/*"])
    raw = json.loads(report.read_text())
    report.unlink()

    envs_by_file = {}
    lines_by_env = {}
    for path in data_files:
        env = path.name.removesuffix(".coverage")
        data = coverage.CoverageData(str(path))
        data.read()
        for file in data.measured_files():
            lines = sorted(data.lines(file) or [])
            if lines:
                envs_by_file.setdefault(file, []).append(env)
                lines_by_env.setdefault(file, {})[env] = lines

    files = {}
    for name in common.source_files():
        entry = raw["files"].get(name)
        if entry is None:
            files[name] = {"measured": False, "envs": []}
            continue
        summary = entry["summary"]
        files[name] = {
            "measured": True,
            "statements": summary["num_statements"],
            "missing_lines": entry["missing_lines"],
            "line_percent": round(
                100.0 * summary["covered_lines"] / summary["num_statements"], 2
            )
            if summary["num_statements"]
            else 100.0,
            "branches": summary["num_branches"],
            "missing_branches": entry.get("missing_branches", []),
            "branch_percent": round(
                100.0 * summary["covered_branches"] / summary["num_branches"], 2
            )
            if summary["num_branches"]
            else 100.0,
            "envs": len(envs_by_file.get(name, [])),
        }

    totals = raw["totals"]
    result = {
        "run": run,
        "commit": common.commit(),
        "envs": len(data_files),
        "totals": {
            "statements": totals["num_statements"],
            "covered_lines": totals["covered_lines"],
            "line_percent": round(
                100.0 * totals["covered_lines"] / totals["num_statements"], 2
            ),
            "branches": totals["num_branches"],
            "covered_branches": totals["covered_branches"],
            "branch_percent": round(
                100.0 * totals["covered_branches"] / totals["num_branches"], 2
            ),
        },
        "files": files,
    }
    (directory / "coverage.json").write_text(json.dumps(result, indent=2) + "\n")
    (directory / "coverage-lines.json").write_text(json.dumps(lines_by_env) + "\n")

    print(f"{'file':<48} {'lines':>7} {'branches':>9} {'envs':>5}  missing")
    for name, entry in files.items():
        if not entry["measured"]:
            print(f"{name:<48} {'-':>7} {'-':>9} {0:>5}  not measured")
            continue
        missing = ",".join(str(line) for line in entry["missing_lines"][:12])
        if len(entry["missing_lines"]) > 12:
            missing += ",..."
        print(
            f"{name:<48} {entry['line_percent']:>6.2f}% {entry['branch_percent']:>8.2f}% "
            f"{entry['envs']:>5}  {missing}"
        )
    t = result["totals"]
    print(
        f"TOTAL lines {t['covered_lines']}/{t['statements']} ({t['line_percent']}%), "
        f"branches {t['covered_branches']}/{t['branches']} ({t['branch_percent']}%), "
        f"{result['envs']} envs, commit {result['commit']}"
    )


if __name__ == "__main__":
    main()
