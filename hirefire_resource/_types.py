from collections.abc import Callable, Coroutine
from typing import Any

Sampler = Callable[[], float | Coroutine[Any, Any, float]]
