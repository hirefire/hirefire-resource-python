"""Shared type aliases for the public surface.

A ``Sampler`` is the no-argument callable a job process supplies to
``config.service`` / ``config.dyno``. It returns the current value of a worker
metric (a non-negative, finite number) each time the dispatcher samples. ``int``
is accepted wherever ``float`` is, per the typing numeric tower, so a sampler may
return either (queue size is an int, queue latency a float).
"""

from collections.abc import Callable

Sampler = Callable[[], float]
