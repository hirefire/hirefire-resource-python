import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common

PASSED_ENV = "PYTEST_ADDOPTS,TOOLS_SEED,TOOLS_RECORD,COVERAGE_FILE,PYTHONPATH"


def read_record(path):
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    failed = []
    sessions = []
    if not path.exists():
        return counts, failed, sessions
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        if "session" in entry:
            sessions.append(entry["session"])
            continue
        outcome = entry["outcome"]
        if outcome == "failed":
            failed.append(f"{entry['id']} ({entry['when']})")
            counts["failed"] += 1
        elif entry["when"] == "call" or outcome == "skipped":
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts, failed, sessions


def run_env(env, root, directory, seed, coverage):
    record = directory / "tests" / f"{env}.jsonl"
    log = directory / "logs" / f"{env}.log"
    for path in (record, log):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)

    addopts = ["-p", "tools_pytest", "-p", "no:cacheprovider"]
    child_env = dict(os.environ)
    child_env.pop("FORCE_COLOR", None)
    child_env["PYTHONPATH"] = str(common.ROOT / "tools" / "lib")
    child_env["TOOLS_RECORD"] = str(record)
    if seed:
        child_env["TOOLS_SEED"] = str(seed)
    else:
        child_env.pop("TOOLS_SEED", None)
    if coverage:
        data = directory / "coverage" / f"{env}.coverage"
        data.parent.mkdir(parents=True, exist_ok=True)
        data.unlink(missing_ok=True)
        child_env["COVERAGE_FILE"] = str(data)
        addopts += [
            "--cov-branch",
            "--cov-context=test",
            f"--cov-config={common.ROOT / 'tools' / 'coverage.ini'}",
        ]
    else:
        child_env["COVERAGE_FILE"] = str(directory / "coverage-scratch" / env)
        (directory / "coverage-scratch").mkdir(parents=True, exist_ok=True)
    child_env["PYTEST_ADDOPTS"] = " ".join(addopts)

    started = time.monotonic()
    with open(log, "w") as output:
        status = subprocess.run(
            ["tox", "run", "-e", env, "-x", f"testenv.pass_env+={PASSED_ENV}"],
            cwd=root,
            env=child_env,
            stdout=output,
            stderr=subprocess.STDOUT,
        ).returncode
    seconds = round(time.monotonic() - started, 1)
    counts, failed, sessions = read_record(record)
    return {
        "env": env,
        "exit": status,
        "seconds": seconds,
        "sessions": sessions,
        **counts,
        "failed_tests": failed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--seed")
    parser.add_argument("--only")
    parser.add_argument("--root", default=str(common.ROOT))
    options = parser.parse_args()

    root = Path(options.root)
    directory = common.results(options.run)
    seed = options.seed
    if seed == "random":
        seed = str(random.SystemRandom().randrange(1, 10**6))
    envs = options.only.split(",") if options.only else common.tox_envs(root)

    started = time.monotonic()
    results = []
    for env in envs:
        result = run_env(env, root, directory, seed, options.coverage)
        results.append(result)
        print(
            f"{env:<22} exit={result['exit']} {result['seconds']:>6}s "
            f"passed={result['passed']} failed={result['failed']} "
            f"skipped={result['skipped']}",
            flush=True,
        )
        for test in result["failed_tests"]:
            print(f"  FAILED {test}", flush=True)

    summary = {
        "run": options.run,
        "commit": common.commit(root),
        "seed": seed,
        "coverage": options.coverage,
        "seconds": round(time.monotonic() - started, 1),
        "envs": len(results),
        "envs_failed": [result["env"] for result in results if result["exit"] != 0],
        "passed": sum(result["passed"] for result in results),
        "failed": sum(result["failed"] for result in results),
        "skipped": sum(result["skipped"] for result in results),
        "results": results,
    }
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"{summary['envs']} envs, {len(summary['envs_failed'])} failed, "
        f"{summary['passed']} passed, {summary['failed']} failed, "
        f"{summary['skipped']} skipped, {summary['seconds']}s, "
        f"commit {summary['commit']}, seed {seed}"
    )
    sys.exit(1 if summary["envs_failed"] else 0)


if __name__ == "__main__":
    main()
