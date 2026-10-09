import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "stress-"
    runs = sorted(
        path
        for path in common.RESULTS.glob(f"{prefix}*/summary.json")
        if path.parent.is_dir()
    )
    if not runs:
        sys.exit(f"no runs named {prefix}* under the results directory")

    flaky = {}
    rows = []
    for path in runs:
        summary = json.loads(path.read_text())
        rows.append(
            {
                "run": summary["run"],
                "commit": summary["commit"],
                "seed": summary["seed"],
                "seconds": summary["seconds"],
                "envs": summary["envs"],
                "envs_failed": summary["envs_failed"],
                "passed": summary["passed"],
                "failed": summary["failed"],
                "skipped": summary["skipped"],
            }
        )
        for result in summary["results"]:
            for test in result["failed_tests"]:
                entry = flaky.setdefault(test, [])
                entry.append(
                    {
                        "run": summary["run"],
                        "env": result["env"],
                        "seed": summary["seed"],
                    }
                )
            if result["exit"] != 0 and not result["failed_tests"]:
                entry = flaky.setdefault(
                    f"<{result['env']} exited {result['exit']}>", []
                )
                entry.append(
                    {
                        "run": summary["run"],
                        "env": result["env"],
                        "seed": summary["seed"],
                    }
                )

    document = {"runs": rows, "failed_tests": flaky}
    (common.RESULTS / f"{prefix}summary.json").write_text(
        json.dumps(document, indent=2) + "\n"
    )

    print(
        f"{'run':<12} {'seed':>7} {'envs':>4} {'failed envs':>11} {'passed':>7} {'failed':>6} {'skipped':>7} {'seconds':>8}"
    )
    for row in rows:
        print(
            f"{row['run']:<12} {str(row['seed']):>7} {row['envs']:>4} {len(row['envs_failed']):>11} "
            f"{row['passed']:>7} {row['failed']:>6} {row['skipped']:>7} {row['seconds']:>8}"
        )
    if not flaky:
        print("no test failed in any run")
    for test, hits in sorted(flaky.items()):
        where = ", ".join(
            f"{hit['run']}/{hit['env']} (seed {hit['seed']})" for hit in hits
        )
        print(f"FLAKY {len(hits)}x {test}: {where}")


if __name__ == "__main__":
    main()
