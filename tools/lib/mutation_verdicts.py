import ast
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LIFECYCLE = {
    "start",
    "ensure_job_queue_loop",
    "stop",
    "running",
    "abandon_inherited_state",
    "_reinit_locks_after_fork",
    "_healthy_running",
    "_healthy_running_locked",
    "_loop_active",
    "_loop_active_locked",
    "_loop_until_stopped",
    "_wait_loop_interval",
    "_join_loop_thread",
    "_loop_live_proc",
    "_reset_dispatch_state_after_fork",
    "_reset_dispatch_state_for_restart",
    "after_fork_in_parent",
    "after_fork_in_child",
    "install_fork_hooks",
    "reinit_after_fork",
    "reinit_locks_after_fork",
    "discard_inherited",
    "_reinit_after_fork",
    "_reset_after_fork",
    "_abandon_inherited_connection",
    "reset_after_fork",
    "reset",
    "_start_if_token",
    "configure",
    "boot",
}
LOG_CALL = re.compile(
    r"delete `(safe_log|self\._log|_log|self\._warn_\w+|probe\.log_to)\("
)
LOG_FUNCTION = re.compile(
    r"^(_warn_\w+_once|_remember_warn|log_to|safe_log|format_error|format_sample_value|_drop_oversized_payload)$"
)

REASONS = {
    "unrun": "the line runs in no env",
    "logs": "a log line or a once-only warning that no test asserts",
    "limits": "a constant or a boundary that no test pins",
    "lifecycle": "a state change of start, stop, or a fork that no test pins",
    "lease": "a lease rule without a test",
    "dispatch": "a dispatch, payload, or transport rule without a test",
    "adapters": "an adapter branch without a test",
    "guards": "a guard or a fallback that no test contradicts",
}
TRUTH = "every caller tests the truth value"
UNREAD = "no caller reads the result"
EQUIVALENT = [
    (
        "client.py",
        {46},
        r"_last_used_at",
        "set again by `_reset_connection` before the first read",
    ),
    ("client.py", {58}, r"`False` to `True`", "the ingest response body is never read"),
    ("client.py", {103}, r"`False` to `True`", "both callers pass `retain_body`"),
    ("client.py", {157, 162}, r"return None in place of `False`", TRUTH),
    (
        "client.py",
        {124},
        r"response\.close",
        "`http.client` closes a response that was read in full, and an error resets the connection",
    ),
    (
        "client.py",
        {175, 176},
        r"= None`",
        "read only while a connection exists, and set again when one is made",
    ),
    (
        "dispatcher.py",
        {15},
        r"TYPE_CHECKING",
        "imports a module that is loaded already",
    ),
    (
        "dispatcher.py",
        {43},
        r"_stopping_flush|`False` to `True`",
        "read only together with `_stopping`, which `stop` sets in the same block",
    ),
    (
        "dispatcher.py",
        {56, 57},
        r"_warned",
        "`start` assigns the map again before a loop runs",
    ),
    (
        "dispatcher.py",
        {61, 70, 121},
        r"return None in place of `False`|`False` to `True`",
        UNREAD + " of `start`",
    ),
    ("dispatcher.py", {369, 395}, r"return None in place of `False`", TRUTH),
    (
        "dispatcher.py",
        {377},
        r"continue in place of break",
        "every later entry checks `live` again and does nothing",
    ),
    (
        "dispatcher.py",
        {555},
        r"delete `data",
        "nothing before the first assignment can raise",
    ),
    (
        "dispatcher.py",
        {45, 95, 236},
        r"^`[01]` to `[12]`$",
        "the generation is only compared for equality",
    ),
    (
        "dispatcher.py",
        {805},
        r"`0\.0` to `1\.0`",
        "a bucket with a count of 0 encodes to `[]` whatever its sum",
    ),
    ("lease.py", {15}, r"`True` to `False`", "nothing assigns to a grant body"),
    (
        "lease.py",
        {18},
        r"`False` to `True`",
        "every grant body is built with an explicit `trace`",
    ),
    (
        "lease.py",
        {32},
        r"`False` to `True`",
        "`trace` is read only while a grant is held, and a grant sets it",
    ),
    (
        "lease.py",
        {37},
        r"job_queues",
        "`start` demotes the lease, which assigns it, before a loop runs",
    ),
    ("lease.py", {38}, r"^`0` to `1`$", "the epoch is only compared for equality"),
    (
        "lease.py",
        {51},
        r"due = False|`False` to `True`",
        "the early return leaves before `due` is read",
    ),
    ("lease.py", {63}, r"epoch = None", "assigned again before its first read"),
    (
        "identity.py",
        {19},
        r"^`1` to `2`$",
        "only the part before the first dot is used",
    ),
    ("buffer.py", {24, 58}, r"str\(strategy\)", "every caller passes a `str`"),
    ("source/job_queues.py", {55}, r"str\(strategy\)", "every caller passes a `str`"),
    ("buffer.py", {99, 100}, r"", "every bucket has both keys"),
    ("probe.py", {43}, r"return None in place of `self`", UNREAD),
    (
        "utility.py",
        {7},
        r"",
        "both branches give the same number for 0 digits, and no caller passes 1",
    ),
    (
        "middleware/__init__.py",
        {30},
        r"present_header",
        "both header readers return a stripped value or `None`",
    ),
    (
        "plan/__init__.py",
        {43},
        r"",
        "`executable` asks `known_adapter` first, and `_load_macro` looks the key up again",
    ),
    ("plan/__init__.py", {57, 84, 91, 220}, r"return None in place of `False`", TRUTH),
    ("plan/__init__.py", {73}, r"", "reached only for a macro that loaded"),
    (
        "plan/__init__.py",
        {237, 246, 249, 251, 257},
        r"",
        UNREAD + " of `_sample_job_strategy`",
    ),
    ("macro/celery.py", {369, 469}, r"", "assigned again before its first read"),
    (
        "macro/dramatiq.py",
        {464},
        r"`<` to `<=`",
        "an equal eta leaves the earliest unchanged",
    ),
    ("macro/rq.py", {66}, r"^`1` to `2`$", "only the first entry of the reply is read"),
]


@lru_cache(maxsize=None)
def functions(file):
    tree = ast.parse((ROOT / file).read_text())
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.lineno, node.end_lineno, node.name))
    return sorted(spans, key=lambda span: span[1] - span[0])


def function_of(file, line):
    for start, end, name in functions(file):
        if start <= line <= end:
            return name
    return ""


def verdict(row):
    file, line = row["file"], row["line"]
    name = function_of(file, line)
    if row["status"] != "uncovered":
        for pattern_file, lines, pattern_description, reason in EQUIVALENT:
            if (
                file.endswith(pattern_file)
                and line in lines
                and re.search(pattern_description, row["description"])
            ):
                return ("equivalent", "equivalent", reason)
    if row["status"] == "uncovered":
        group = "unrun"
    elif LOG_CALL.search(row["description"]) or LOG_FUNCTION.match(name):
        group = "logs"
    elif row["operator"] == "number":
        group = "limits"
    elif "/macro/" in file:
        group = "adapters"
    elif name in LIFECYCLE:
        group = "lifecycle"
    elif file.endswith("lease.py"):
        group = "lease"
    elif file.endswith(("dispatcher.py", "client.py", "buffer.py", "probe.py")):
        group = "dispatch"
    else:
        group = "guards"
    return ("test gap", group, REASONS[group])
