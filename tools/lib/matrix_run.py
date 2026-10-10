import argparse
import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common

PASSED_ENV = "PYTEST_ADDOPTS,TOOLS_SEED,TOOLS_RECORD,COVERAGE_FILE,PYTHONPATH"
SERVICE_ENV = "REDIS_URL,AMQP_URL"
MAX_LANES = 4


def compose(lane, *arguments):
    base = subprocess.run(
        ["bin/worktree", "compose-project"],
        cwd=common.ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return subprocess.run(
        ["docker", "compose", "-p", f"{base}-lane{lane}", *arguments],
        cwd=common.ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def lane_services(lane):
    if lane == 0:
        return {}
    compose(lane, "up", "-d", "--wait")
    redis = compose(lane, "port", "redis", "6379").rsplit(":", 1)[1]
    rabbitmq = compose(lane, "port", "rabbitmq", "5672").rsplit(":", 1)[1]
    return {
        "REDIS_PORT": redis,
        "RABBITMQ_PORT": rabbitmq,
        "REDIS_URL": f"redis://127.0.0.1:{redis}/0",
        "AMQP_URL": f"amqp://guest:guest@127.0.0.1:{rabbitmq}",
    }


def build_package(root, directory):
    target = directory / "package"
    for old in target.glob("*.tar.gz"):
        old.unlink()
    subprocess.run(
        ["poetry", "build", "--format", "sdist", "--output", str(target)],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return str(next(target.glob("*.tar.gz")))


def lanes(envs, jobs):
    by_python = {}
    for env in envs:
        by_python.setdefault(env.split("-", 1)[0], []).append(env)
    result = [[] for _ in range(min(jobs, MAX_LANES, len(by_python)))]
    for group in sorted(by_python.values(), key=len, reverse=True):
        min(result, key=len).extend(group)
    return result


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


def run_env(env, root, directory, seed, coverage, services=None, package=None):
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
    passed_env = PASSED_ENV
    if services:
        child_env.update(services)
        passed_env += f",{SERVICE_ENV}"
    command = ["tox", "run", "-e", env, "-x", f"testenv.pass_env+={passed_env}"]
    if package:
        command += ["--installpkg", package]

    started = time.monotonic()
    with open(log, "w") as output:
        status = subprocess.run(
            command,
            cwd=root,
            env=child_env,
            stdout=output,
            stderr=subprocess.STDOUT,
        ).returncode
    seconds = round(time.monotonic() - started, 1)
    counts, failed, sessions = read_record(record)
    result = {
        "env": env,
        "exit": status,
        "seconds": seconds,
        "sessions": sessions,
        **counts,
        "failed_tests": failed,
    }
    lines = [
        f"{env:<22} exit={status} {seconds:>6}s passed={counts['passed']} "
        f"failed={counts['failed']} skipped={counts['skipped']}"
    ]
    lines += [f"  FAILED {test}" for test in failed]
    print("\n".join(lines), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", nargs="?")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--seed")
    parser.add_argument("--only")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--down", action="store_true")
    parser.add_argument("--root", default=str(common.ROOT))
    options = parser.parse_args()
    if options.down:
        for lane in range(1, MAX_LANES):
            compose(lane, "down", "--volumes")
        return
    if not options.run:
        parser.error("a run name is required")

    root = Path(options.root)
    directory = common.results(options.run)
    seed = options.seed
    if seed == "random":
        seed = str(random.SystemRandom().randrange(1, 10**6))
    envs = options.only.split(",") if options.only else common.tox_envs(root)

    started = time.monotonic()
    if options.jobs > 1:
        package = build_package(root, directory)

        def run_lane(numbered):
            services = lane_services(numbered[0])
            return [
                run_env(env, root, directory, seed, options.coverage, services, package)
                for env in numbered[1]
            ]

        groups = lanes(envs, options.jobs)
        with ThreadPoolExecutor(len(groups)) as pool:
            results = [
                result
                for lane in pool.map(run_lane, enumerate(groups))
                for result in lane
            ]
    else:
        results = [
            run_env(env, root, directory, seed, options.coverage) for env in envs
        ]

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
