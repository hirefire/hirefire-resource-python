"""Public API carries PEP 484 annotations; the package ships py.typed."""

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path

import hirefire_resource
from hirefire_resource._types import Sampler
from hirefire_resource.configuration import Configuration
from hirefire_resource.hirefire import HireFire


def _unwrap_classmethod(fn):
    raw = HireFire.__dict__[fn.__name__]

    inner = raw.__func__

    return inspect.unwrap(inner)


def test_py_typed_marker_is_shipped_next_to_the_package():
    marker = Path(hirefire_resource.__file__).resolve().parent / "py.typed"

    assert marker.is_file(), marker


def test_configure_has_inline_return_annotation():
    hints = inspect.get_annotations(_unwrap_classmethod(HireFire.configure))

    assert hints["return"] == Iterator[Configuration]


def test_boot_has_inline_return_annotation():
    hints = inspect.get_annotations(_unwrap_classmethod(HireFire.boot))

    assert hints["return"] is Configuration


def test_dyno_has_inline_parameter_and_return_annotations():
    hints = inspect.get_annotations(Configuration.dyno)

    assert hints["name"] is str

    assert hints["sampler"] == Sampler | None

    assert hints["return"] is None


def test_job_queue_macros_have_inline_annotations_in_source():
    root = Path(hirefire_resource.__file__).resolve().parent / "macro"

    paths = [root / "celery.py", root / "rq.py", root / "dramatiq.py"]

    found = 0

    for path in paths:
        tree = ast.parse(path.read_text())

        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            if not (
                node.name.startswith("job_queue")
                or node.name.startswith("async_job_queue")
            ):
                continue

            found += 1

            assert node.returns is not None, f"{path.name}:{node.name} missing return"

            assert node.args.vararg is not None, f"{path.name}:{node.name} *queues"

            assert (
                node.args.vararg.annotation is not None
            ), f"{path.name}:{node.name} untyped *queues"

            kwonly = {
                arg.arg: arg.annotation is not None for arg in node.args.kwonlyargs
            }
            assert kwonly, f"{path.name}:{node.name} missing keyword-only options"
            assert all(kwonly.values()), f"{path.name}:{node.name} untyped keyword-only"

    assert found == 14, found
