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
LENS_CONNECTIVES = ["Celery's prefork pool"]
LENS_POINTS = {
    "Logic": [
        ("`bool` passing as `int`", r"^`bool` passing as `int`$"),
        ("`None`", r"^`None`$"),
        (
            "NaN and infinity (which `json.dumps` writes as tokens that are not JSON)",
            r"^NaN and infinity$",
        ),
        ("`Decimal` and `Fraction` samples", r"^`Decimal` and `Fraction`$"),
        ("`round` and integer division", r"^`round` and integer division$"),
        ("naive and aware datetimes", r"^Naive and aware datetimes$"),
        (
            "the wall clock against the monotonic clock",
            r"^Wall clock against monotonic clock$",
        ),
        ("mutable default arguments", r"^Mutable default arguments$"),
        (
            "the truth value of 0 and of an empty container",
            r"^Truth value of 0 and of an empty container$",
        ),
    ],
    "Concurrency and fork": [
        (
            "a lock held at the moment of a fork and the `os.register_at_fork` hooks",
            r"^A lock held at the moment of a fork, and the hooks$",
        ),
        (
            "a host that forks without running those hooks or without thread support",
            r"^A host that forks without the hooks or without thread support$",
        ),
        ("Gunicorn with and without `--preload`", r"^Gunicorn with and without"),
        (
            "the warning that Python 3.12 and later give when a process with "
            "threads forks",
            r"^The warning of Python 3.12 and later$",
        ),
        (
            "the `multiprocessing` start methods and Celery's prefork pool",
            r"^`multiprocessing` start methods and the prefork pool of Celery$",
        ),
        ("gevent and eventlet workers", r"^gevent and eventlet workers$"),
        ("daemon threads at interpreter exit", r"^Daemon threads at interpreter exit$"),
        ("the Celery held-task thread", r"^The Celery held-task thread$"),
        (
            "an async sampler that runs in an event loop of its own on the "
            "job-queue thread",
            r"^An async sampler on the job-queue thread$",
        ),
        ("builds without the GIL", r"^Builds without the GIL$"),
    ],
    "Host safety": [
        (
            "the WSGI and the ASGI request path",
            r"^The WSGI and the ASGI request path$",
        ),
        ("a blocking call on the event loop", r"^A blocking call on the event loop$"),
        (
            "ASGI scopes that are not HTTP and streaming responses",
            r"^ASGI scopes that are not HTTP, and streaming responses$",
        ),
        ("work done at import", r"^Work done at import$"),
        (
            "the `BaseException` kinds that must pass through (`KeyboardInterrupt`, "
            "`SystemExit`, `GeneratorExit`, `asyncio.CancelledError`)",
            r"^The `BaseException` kinds that must pass through$",
        ),
        (
            "a host that turns warnings into errors",
            r"^A host that turns warnings into errors$",
        ),
        ("the `logging` setup of the host", r"^The `logging` setup of the host$"),
    ],
    "Contract and integrator experience": [
        ("the 1.x names of `v1.0.4`", r"^The 1.x names of `v1.0.4`$"),
        ("`configure` as a context manager", r"^`configure` as a context manager$"),
        ("the async variants of the macros", r"^The async variants of the macros$"),
        (
            "the error messages and the log output",
            r"^The error messages and the log output$",
        ),
        (
            "the type hints that the checker of a consumer reads",
            r"^The type hints a consumer reads$",
        ),
    ],
    "Adapters": [
        ("Celery on Redis and on AMQP", r"^Celery on Redis and on AMQP$"),
        ("priorities", r"^Celery priorities$"),
        ("ETA and countdown", r"^Celery ETA and countdown$"),
        ("retries", r"^Celery retries$"),
        ("late acknowledgment", r"^Late acknowledgment, unacknowledged tasks"),
        ("unacknowledged tasks", r"^Late acknowledgment, unacknowledged tasks"),
        ("held tasks from worker broadcasts", r"unacknowledged tasks, held tasks$"),
        ("RQ (its registries and `rq:wip`)", r"^RQ registries and `rq:wip`$"),
        ("Dramatiq on Redis and on RabbitMQ", r"^Dramatiq on Redis and on RabbitMQ$"),
        ("delay and dead-letter queues", r"^Dramatiq delay and dead-letter queues$"),
        ("Redis replies as bytes or as text", r"^Redis replies as bytes or text$"),
        ("broker URLs with TLS", r"^Broker URLs with TLS$"),
        ("credentials", r"^Credentials$"),
        ("sentinel", r"^Sentinel and cluster$"),
        ("cluster", r"^Sentinel and cluster$"),
        ("connection reuse and timeouts", r"^Connection reuse and timeouts$"),
        (
            "the cost of each query on a long queue",
            r"^The cost of each query on a long queue$",
        ),
    ],
    "Architecture": [
        ("the `plan` package", r"^The `plan` package$"),
        ("the fork reset paths", r"^The fork reset paths$"),
        (
            "every place where the module map differs from the one Ruby has after "
            "its fix pass ([map](../ruby-audit/fixes.md#design)), judged by the "
            "[consistency rule](../AGENTS.md#cross-runtime-consistency)",
            r"^Where the module map differs from Ruby$",
        ),
    ],
    "Code quality": [
        ("`Any` where a type exists", r"^`Any` where a type exists$"),
        ("a helper that only a test calls", r"^A helper that only a test calls$"),
        (
            "scaffolding kept by decision "
            "([`plan/size_only.py`](../python.md#documentation))",
            r"^Scaffolding kept by decision$",
        ),
    ],
    "Performance": [
        (
            "the cost per request in WSGI and in ASGI",
            r"^The cost per request in WSGI and in ASGI$",
        ),
        (
            "lock contention with and without the GIL",
            r"^Lock contention with and without the GIL$",
        ),
        (
            "thread wakeups per second while idle",
            r"^Thread wakeups per second while idle$",
        ),
        ("the cost of importing the package", r"^The cost of importing the package$"),
        ("the Celery worker broadcast", r"^The Celery worker broadcast$"),
        ("memory bounds", r"^Memory bounds$"),
    ],
    "Security": [
        (
            "the token in a log line, a `repr`, an exception message, or a traceback",
            r"^The token in a log line, a `repr`, an exception message, or a traceback$",
        ),
        (
            "the certificate checks of `http.client` and `ssl`",
            r"^The certificate checks of `http.client` and `ssl`$",
        ),
        ("the proxy variables of the environment", r"^The proxy variables$"),
        ("redirects", r"^Redirects$"),
        (
            "a response body that is large or nested deeply enough to reach the "
            "recursion limit",
            r"^A response body that is large or deeply nested$",
        ),
        (
            "a broker URL with credentials in a log line",
            r"^A broker URL with credentials in a log line$",
        ),
    ],
    "Tests": [
        ("a test file that no tox env runs", r"^A test file that no tox env runs$"),
        ("a source file that no env covers", r"^A source file that no env covers$"),
        (
            "a test that depends on the order of the tests",
            r"^A test that depends on the order of the tests$",
        ),
        (
            "mocket and `freeze_time` patterns that hide a failure",
            r"^mocket and `freeze_time` patterns that hide a failure$",
        ),
        (
            "tests that reach into private names",
            r"^Tests that reach into private names$",
        ),
        ("the pytest-asyncio settings", r"^The pytest-asyncio settings$"),
    ],
    "Packaging and docs": [
        (
            "the content of the wheel and the sdist",
            r"^The content of the wheel and the sdist$",
        ),
        ("`py.typed`", r"^`py.typed`$"),
        (
            "`requires-python` and the classifiers",
            r"^`requires-python` and the classifiers$",
        ),
        (
            "an import with no optional library installed",
            r"^An import with no optional library installed$",
        ),
        (
            "warnings under `python -W error` and `-X dev`",
            r"^Warnings under `python -W error` and `-X dev`$",
        ),
        (
            "syntax and standard library use against the 3.11 floor",
            r"^Syntax and standard library use against the 3.11 floor$",
        ),
        ("CI", r"^CI$"),
        ("the README and the CHANGELOG", r"^The README and the CHANGELOG$"),
        (
            "the types as mypy and Pyright see them from a consumer project",
            r"^The types as mypy and Pyright see them from a consumer project$",
        ),
    ],
}
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


def plan_points(directory, lens, errors):
    plan = (directory / "README.md").read_text()
    match = re.search(rf"^- \*\*{re.escape(lens)}:\*\* .*? Python: (.+)$", plan, re.M)
    if not match:
        errors.append(f"lens {lens}: no Python points in the plan")
        return
    rest = match.group(1)
    for point, _label in sorted(LENS_POINTS[lens], key=lambda item: -len(item[0])):
        if point not in rest:
            errors.append(f"lens {lens}: the plan does not name {point!r}")
        rest = rest.replace(point, "", 1)
    for connective in LENS_CONNECTIVES:
        rest = rest.replace(connective, "")
    rest = re.sub(r"\band\b|\bor\b|[\s,.()]", "", rest)
    if rest:
        errors.append(f"lens {lens}: plan points without an entry: {rest!r}")


def check_lenses(directory, errors):
    text = (directory / "lenses.md").read_text()
    sections = re.findall(r"^## (.+)$", text, re.M)
    if sections != LENSES:
        errors.append(f"lens sections are {sections}")
    answered = total = 0
    lines = []
    for lens in LENSES:
        before = answered
        plan_points(directory, lens, errors)
        parts = re.split(rf"^## {re.escape(lens)}$", text, flags=re.M)
        body = parts[1].split("\n## ")[0] if len(parts) == 2 else ""
        if not re.search(r"^Examined: .{40,}", body, re.M):
            errors.append(f"lens {lens}: no line that says what was examined and how")
        bullets = re.findall(r"^- \*\*(.+?):\*\* (.+)$", body, re.M)
        for point, label in LENS_POINTS[lens]:
            total += 1
            answers = [answer for name, answer in bullets if re.search(label, name)]
            if len(answers) != 1:
                errors.append(f"lens {lens}: {len(answers)} bullets for {point!r}")
            elif len(answers[0]) < 40 or not re.search(
                r"P-\d\d|[Nn]othing found", answers[0]
            ):
                errors.append(
                    f"lens {lens}: the answer for {point!r} names no finding "
                    "and does not say that nothing was found"
                )
            else:
                answered += 1
        named = sorted(set(re.findall(r"P-\d\d", body)))
        lines.append(
            f"  {lens}: {answered - before} of {len(LENS_POINTS[lens])} points, "
            f"{len(named)} findings named"
        )
    return len(sections), answered, total, lines


def main():
    directory = Path(sys.argv[1]).resolve()
    errors = []
    items = check_findings(directory, errors)
    mapped, found = check_map(directory, errors)
    rows, counts = check_comparison(directory, {i for i, *_ in items}, errors)
    lenses, answered, points, lens_lines = check_lenses(directory, errors)
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
    print(
        f"lenses: {lenses} sections, {answered} of {points} Python points of the plan "
        "have an answer that names a finding or says that nothing was found"
    )
    for line in lens_lines:
        print(line)
    for error in errors:
        print(f"ERROR {error}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
