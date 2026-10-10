import ast
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TRUTH = "every caller tests the truth value"
WATCH = "only a watch reads the flag, and a watch starts after a fork has set it"
CONNECTION = "the value is read only while the client holds a connection, and opening one sets it"
MEMORY = "the line only lets go of a session that nothing asks again"

EQUIVALENT: list[tuple[str, str, str, str, str]] = [
    (
        "dispatcher/session.py",
        "__init__",
        "self._handoff = False",
        r"delete|`False` to `True`",
        "a loop ends only after halt, which assigns the flag before the loop reads it",
    ),
    ("hirefire.py", "", "_handed_over = False", r"`False` to `True`", WATCH),
    (
        "hirefire.py",
        "_wait_for_children",
        "cls._handed_over = False",
        r"delete|`False` to `True`",
        WATCH,
    ),
    (
        "hirefire.py",
        "_handoff_settled",
        "cls._handed_over = False",
        r"delete|`False` to `True`",
        WATCH,
    ),
    ("hirefire.py", "_handoff_settled", "return False", r"return None", TRUTH),
    ("hirefire.py", "_children_left", "return False", r"return None", TRUTH),
    (
        "hirefire.py",
        "_children_left",
        "posix.waitid(posix.P_ALL, 0,",
        r"`0` to `1`",
        "waitid ignores the id when it is asked for every child",
    ),
    (
        "lease.py",
        "__init__",
        "self._epoch = 0",
        r"`0` to `1`",
        "the epoch is only compared for equality with an earlier reading of itself",
    ),
    ("client.py", "__init__", "self._last_used_at", r"delete", CONNECTION),
    ("client.py", "discard_inherited", "self._owner_pid = None", r"delete", CONNECTION),
    (
        "client.py",
        "discard_inherited",
        "self._last_used_at = None",
        r"delete",
        CONNECTION,
    ),
    ("client.py", "_reset_connection", "self._owner_pid = None", r"delete", CONNECTION),
    (
        "client.py",
        "_reset_connection",
        "self._last_used_at = None",
        r"delete",
        CONNECTION,
    ),
    (
        "client.py",
        "_perform",
        "response.close()",
        r"delete",
        "a body that was read to its end has closed the response, and every other "
        "path closes the connection",
    ),
    (
        "client.py",
        "_read_body",
        "announced = response.length or 0",
        r"`0` to `1`",
        "a length of one byte is under the limit as well",
    ),
    (
        "client.py",
        "_read_body",
        'body = b""',
        r"delete",
        "the length of the body is not looked at when the announced length is over the limit",
    ),
    (
        "client.py",
        "_read_body",
        "self.MAX_BODY_BYTES + 1",
        r"`1` to `2`",
        "one byte past the limit shows that a body is too long, and a second one adds nothing",
    ),
    ("client.py", "_reusable", "return False", r"return None", TRUTH),
    (
        "buffer.py",
        "",
        "EMPTY_BUCKET",
        r"`0.0` to `1.0`",
        "the sum of a bucket with a count of 0 is never read",
    ),
    (
        "once.py",
        "log",
        "seen[key] = True",
        r"`True` to `False`",
        "only the presence of the key is read",
    ),
    (
        "plan/entry.py",
        "__init__",
        "self._macro",
        r"delete",
        "the macro is read only for an entry whose adapter check has loaded it",
    ),
    (
        "middleware/__init__.py",
        "calculate_request_queue_time",
        "1e17",
        r"`1e17` to `1e\+17`",
        "both literals are the same number",
    ),
    (
        "source/cpu/__init__.py",
        "sample",
        "usage is None",
        r"`or` to `and`",
        "a reading has a usage and a source or neither, so a reading without a usage "
        "differs in its source from one with a usage",
    ),
    (
        "source/cpu/usage.py",
        "stat_ticks",
        "content[close + 1 :]",
        r"`1` to `2`",
        "a space follows the parenthesis, and split drops it",
    ),
    (
        "macro/rq.py",
        "job_queue_latency",
        "num=1",
        r"`1` to `2`",
        "only the first scheduled job of the answer is read",
    ),
    (
        "macro/celery.py",
        "__init__",
        "self._counted_at = 0.0",
        r"delete|`0.0` to `1.0`",
        "the time is read only when a count exists, and a refresh sets both",
    ),
    (
        "macro/celery.py",
        "__init__",
        "self._ends = 0.0",
        r"delete|`0.0` to `1.0`",
        "events are drained only inside within_budget, which sets the end first",
    ),
    (
        "macro/dramatiq.py",
        "_redis_dq_stats",
        "eta < earliest",
        r"`<` to `<=`",
        "an eta equal to the earliest one assigns the same value",
    ),
]
ACCEPTED: list[tuple[str, str, str, str, str]] = [
    ("dispatcher/__init__.py", "start", "self._retired = None", r"delete", MEMORY),
    (
        "dispatcher/__init__.py",
        "abandon_inherited_state",
        "self._retired = None",
        r"delete",
        MEMORY,
    ),
    (
        "dispatcher/__init__.py",
        "stop",
        "self._session = None",
        r"delete",
        "a halted session is not live, so every reader treats it as no session. The "
        "line closes a race between two stops, and the Ruby pass accepted the same",
    ),
    (
        "lease.py",
        "",
        "@dataclass(frozen=True)",
        r"`True` to `False`",
        "a guard for the reader of the code: nothing assigns to a grant body",
    ),
]


@lru_cache(maxsize=None)
def source(file):
    return (ROOT / file).read_text().splitlines()


@lru_cache(maxsize=None)
def functions(file):
    tree = ast.parse("\n".join(source(file)))
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


def matches(row, entry):
    file, function, text, description, _reason = entry
    return (
        row["file"].endswith(file)
        and function == function_of(row["file"], row["line"])
        and text in source(row["file"])[row["line"] - 1]
        and re.search(description, row["description"]) is not None
    )


def verdict(row):
    if row["status"] != "uncovered":
        for entry in EQUIVALENT:
            if matches(row, entry):
                return ("equivalent", entry[4])
    for entry in ACCEPTED:
        if matches(row, entry):
            return ("accepted", entry[4])
    return None
