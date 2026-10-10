import argparse
import ast
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coverage
import mutation_generator
import tools_common as common

ORDER = [
    "core",
    "flask3",
    "flask2",
    "starlette1",
    "starlette",
    "fastapi",
    "quart",
    "django6",
    "django5",
    "django4",
    "rq2",
    "rq",
    "dramatiq",
    "celery",
]
TIMEOUTS = {"celery": 240}
DEFAULT_TIMEOUT = 90
HARNESS_EXIT = {4, 5}
FAILED = re.compile(r"^FAILED (\S+)", re.M)


def cells():
    newest = {}
    for env in common.tox_envs():
        version, factor = env.split("-", 1)
        if factor not in newest or version > newest[factor]:
            newest[factor] = version
    return [f"{newest[factor]}-{factor}" for factor in ORDER if factor in newest]


def base_id(context):
    node = context.rsplit("|", 1)[0]
    return re.sub(r"\[.*\]\Z", "", node)


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@lru_cache(maxsize=None)
def statements(file):
    found = []

    def visit(node, inside_function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                found.append((child.lineno, child.end_lineno, inside_function))
            visit(child, inside_function or isinstance(child, FUNCTIONS))

    visit(ast.parse((common.ROOT / file).read_text()), False)
    return sorted(found, key=lambda span: span[1] - span[0])


def statement(file, line):
    for start, end, inside_function in statements(file):
        if start <= line <= end:
            return start, not inside_function
    return line, False


class Coverage:
    def __init__(self, run, envs):
        self.by_env = {}
        self.files_by_env = {}
        for env in envs:
            path = common.RESULTS / run / "coverage" / f"{env}.coverage"
            data = coverage.CoverageData(str(path))
            data.read()
            per_file = {}
            for file in data.measured_files():
                if not file.startswith("hirefire_resource/"):
                    continue
                lines = {}
                for line, contexts in data.contexts_by_lineno(file).items():
                    lines[line] = sorted({base_id(c) for c in contexts if c})
                per_file[file] = lines
            self.by_env[env] = per_file
            tests = set()
            record = common.RESULTS / run / "tests" / f"{env}.jsonl"
            for line in record.read_text().splitlines():
                entry = json.loads(line)
                if "id" in entry:
                    tests.add(entry["id"].split("::")[0])
            self.files_by_env[env] = sorted(tests)

    def plan(self, mutant):
        start, at_import = statement(mutant.file, mutant.line)
        plan = []
        for env, per_file in self.by_env.items():
            lines = per_file.get(mutant.file)
            if lines is None:
                continue
            line = mutant.line if mutant.line in lines else start
            if line not in lines:
                continue
            tests = self.files_by_env[env] if at_import else lines[line]
            if not tests:
                touched = sorted({t for ts in lines.values() for t in ts})
                tests = touched or self.files_by_env[env]
            if len(tests) > 120:
                tests = sorted({test.split("::")[0] for test in tests})
            plan.append((env, tests))
        return plan


def run_tests(env, tests, sandbox):
    python = common.ROOT / ".tox" / env / "bin" / "python"
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("HIREFIRE_") and key not in ("FORCE_COLOR", "DYNO")
    }
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    child_env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
    timeout = TIMEOUTS.get(env.split("-", 1)[1], DEFAULT_TIMEOUT)
    with tempfile.TemporaryFile("w+") as output:
        process = subprocess.Popen(
            [
                str(python),
                "-B",
                "-m",
                "pytest",
                "-x",
                "-q",
                "-rf",
                "-p",
                "no:cacheprovider",
                "-p",
                "mutation_warmup",
                *tests,
            ],
            cwd=sandbox,
            env=child_env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            return "timeout", None
        output.seek(0)
        failed = FAILED.search(output.read())
    return code, failed.group(1) if failed else None


def run_plan(env, tests, sandbox):
    code, failed = run_tests(env, tests, sandbox)
    if code in HARNESS_EXIT:
        tests = sorted({test.split("::")[0] for test in tests})
        code, failed = run_tests(env, tests, sandbox)
    return code, failed, tests


def run_controls(plans, sandboxes, directory):
    work = queue.Queue()
    for plan in plans:
        work.put(plan)
    total = work.qsize()
    record = directory / "controls.jsonl"
    record.write_text("")
    lock = threading.Lock()
    failures = []
    counter = {"n": 0}

    def worker(sandbox):
        while True:
            try:
                env, tests = work.get_nowait()
            except queue.Empty:
                return
            started = time.monotonic()
            code, failed, _ = run_plan(env, list(tests), sandbox)
            entry = {
                "env": env,
                "tests": len(tests),
                "first_test": tests[0],
                "code": code,
                "failed": failed,
                "seconds": round(time.monotonic() - started, 1),
            }
            with lock:
                with open(record, "a") as file:
                    file.write(json.dumps(entry) + "\n")
                if code != 0:
                    failures.append(entry)
                counter["n"] += 1
                if counter["n"] % 100 == 0:
                    print(f"controls {counter['n']}/{total}", flush=True)

    threads = [
        threading.Thread(target=worker, args=(sandbox,)) for sandbox in sandboxes
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"controls: {total} test sets on the unchanged tree, {len(failures)} failed")
    return failures


def evaluate(mutant, plan, sandbox, original):
    target = Path(sandbox) / mutant.file
    target.write_text(mutant.apply(original))
    started = time.monotonic()
    outcome = {"status": "survived", "env": None, "envs_run": 0}
    try:
        for env, tests in plan:
            code, failed, tests = run_plan(env, tests, sandbox)
            outcome["envs_run"] += 1
            if code == "timeout":
                outcome.update(status="timeout", env=env)
                break
            if code in HARNESS_EXIT:
                outcome.update(status="error", env=env, code=code)
                break
            if code != 0:
                again, _ = run_tests(env, tests, sandbox)
                if again == 0:
                    outcome["kills_not_repeated"] = (
                        outcome.get("kills_not_repeated", 0) + 1
                    )
                    continue
                outcome.update(status="killed", env=env, test=failed)
                break
    finally:
        target.write_text(original)
    outcome["seconds"] = round(time.monotonic() - started, 1)
    return outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["count", "check", "run"])
    parser.add_argument("--coverage-run", default="baseline")
    parser.add_argument("--name", default="mutation")
    parser.add_argument("--sandboxes", default="")
    parser.add_argument("--only")
    parser.add_argument("--limit", type=int)
    options = parser.parse_args()

    envs = cells()
    sources = {file: (common.ROOT / file).read_text() for file in common.source_files()}
    mutants = []
    for file, source in sources.items():
        if options.only and options.only not in file:
            continue
        mutants.extend(mutation_generator.generate(file, source))
    if options.limit:
        mutants = mutants[:: max(1, len(mutants) // options.limit)]

    if options.command == "count":
        print(
            f"{len(mutants)} changes in {len(sources)} files, cells: {' '.join(envs)}"
        )
        return

    sandboxes = [path for path in options.sandboxes.split(",") if path]
    if not sandboxes:
        sys.exit("pass --sandboxes with one directory per worker")

    data = Coverage(options.coverage_run, envs)
    if options.command == "check":
        failed = False
        for env in envs:
            code, _ = run_tests(env, data.files_by_env[env], sandboxes[0])
            print(f"{env:<18} unchanged tree exit={code}", flush=True)
            failed = failed or code != 0
        sys.exit(1 if failed else 0)

    directory = common.results(options.name)
    record = directory / "mutants.jsonl"
    done = set()
    if record.exists():
        for line in record.read_text().splitlines():
            done.add(json.loads(line)["key"])
    work = queue.Queue()
    plans = {}
    for mutant in mutants:
        if mutant.key not in done:
            work.put(mutant)
            for env, tests in data.plan(mutant):
                plans[(env, tuple(tests))] = True
    failures = run_controls(plans, sandboxes, directory)
    if failures:
        for entry in failures:
            print(f"CONTROL FAILED {entry['env']} {entry['failed']}")
        sys.exit(1)
    total = work.qsize()
    lock = threading.Lock()
    counter = {"n": 0}

    def worker(sandbox):
        while True:
            try:
                mutant = work.get_nowait()
            except queue.Empty:
                return
            plan = data.plan(mutant)
            if not plan:
                outcome = {
                    "status": "uncovered",
                    "env": None,
                    "envs_run": 0,
                    "seconds": 0,
                }
            else:
                outcome = evaluate(mutant, plan, sandbox, sources[mutant.file])
            entry = {
                "key": mutant.key,
                "file": mutant.file,
                "line": mutant.line,
                "operator": mutant.operator,
                "description": mutant.description,
                **outcome,
            }
            with lock:
                with open(record, "a") as file:
                    file.write(json.dumps(entry) + "\n")
                counter["n"] += 1
                if counter["n"] % 100 == 0:
                    print(f"{counter['n']}/{total}", flush=True)

    threads = [
        threading.Thread(target=worker, args=(sandbox,)) for sandbox in sandboxes
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"done: {total} changes recorded in {record.relative_to(common.RESULTS)}")


if __name__ == "__main__":
    main()
