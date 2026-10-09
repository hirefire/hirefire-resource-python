import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common


def senders(mapping):
    if not mapping:
        return "nobody"
    groups = {}
    for name, entry in sorted(mapping.items()):
        key = (name.split()[0], "+".join(entry["series"]))
        groups.setdefault(key, []).append(str(entry["dispatches"]))
    return ", ".join(
        f"{len(counts)} {role} ({series}: {'/'.join(counts)})"
        for (role, series), counts in groups.items()
    )


def yes(value):
    return {True: "yes", False: "no", None: "n/a"}[value]


def warning(line):
    match = re.search(r"\w+Warning", line)
    return match.group(0) if match else line[:70]


def host(row):
    if "harness_error" in row:
        print(f"{row['host']} | harness error: {row['harness_error']}")
        return
    threads = row.get("thread_support_lines") or [
        line for line in row["boot_warnings"] if "threads support" in line
    ]
    warnings = sorted(
        {
            warning(line)
            for line in row["boot_warnings"]
            if "threads support" not in line
        }
        - {"warnings.warn(SecurityWarning"}
    )
    first = (
        f"{row['host']} | warnings at boot: {', '.join(warnings) or 'none'}"
        f" | the client boots in: {', '.join(row['processes_that_booted']) or 'nobody'}"
    )
    for line in threads:
        first += f" | uWSGI: {line.strip('* ')}"
    print(first)
    second = f"  sends before a request: {senders(row['sent_before_first_request'])}"
    restart = row.get("worker_restart") or {}
    starts = yes(restart.get("new_worker_started_dispatcher"))
    sends = yes(restart.get("new_worker_sends"))
    if "requests_answered" in row:
        print(
            f"{second} | answered {row['requests_answered']} of 20"
            f" | sends after: {senders(row['senders_after_requests'])}"
        )
        third = (
            f"  replaced worker: answered {restart.get('requests_answered')} of 20, "
            f"starts a dispatcher {starts}, sends {sends}"
        )
    else:
        print(second)
        third = f"  replaced pool child: starts a dispatcher {starts}, sends {sends}"
    third += f" | stop: {row['shutdown_seconds']} s"
    if row.get("killed_after_20s"):
        third += " (killed after 20 s)"
    errors = sorted(
        {
            match.group(0)
            for line in row["error_lines"]
            if (match := re.search(r"\w+(Error|Warning)\b(: [^\[]{0,40})?", line))
        }
    )
    print(third + (f" | error lines: {' / '.join(errors)}" if errors else ""))


def race(rows):
    print("boots with all 10 requests answered, with a token (without)")
    for name in dict.fromkeys(row["host"] for row in rows):
        pair = {row["hirefire_token"]: row for row in rows if row["host"] == name}
        if True not in pair or False not in pair:
            continue
        line = (
            f"  {name:<36}"
            f"{pair[True]['runs_with_all_10_requests_answered']} of {pair[True]['runs']}"
            f" ({pair[False]['runs_with_all_10_requests_answered']} of {pair[False]['runs']})"
            f", none answered in {pair[True]['runs_with_no_request_answered']}"
            f", idna error in {pair[True]['runs_with_idna_error']}"
        )
        if "runs_stopped_within_10s" in pair[True]:
            line += f", stopped within 10 s in {pair[True]['runs_stopped_within_10s']}"
        print(line)


def main():
    for name in sys.argv[1:] or ["hosts"]:
        directory = common.results(name)
        versions = (directory / "versions.txt").read_text().split("\n")
        wanted = ("python", "gunicorn", "gevent", "uvicorn", "uWSGI", "celery")
        print(
            f"== {name}: "
            + ", ".join(line for line in versions if line.startswith(wanted))
        )
        for row in json.loads((directory / "hosts.json").read_text()):
            host(row)
        path = directory / "preload-race.json"
        if path.exists():
            race(json.loads(path.read_text()))


if __name__ == "__main__":
    main()
