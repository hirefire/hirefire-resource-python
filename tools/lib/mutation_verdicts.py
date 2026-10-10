import ast
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TRUTH = "every caller tests the truth value"
UNREAD = "no caller reads the result"

EQUIVALENT: list[tuple[str, str, str, str, str]] = []
ACCEPTED: list[tuple[str, str, str, str, str]] = []


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
