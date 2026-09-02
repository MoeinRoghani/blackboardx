"""The retry policy both halves use.

One implementation so the blackboard and its agents wait the same way.
:mod:`blackboard.delivery` and :mod:`blackboard.agent` each export
``default_backoff`` from here, and it is the same object under both names.
"""

from __future__ import annotations

import random

#: The longest the default policy waits between attempts, in seconds.
MAX_BACKOFF = 30.0


def default_backoff(attempt: int, retry_after: float | None) -> float:
    """Returns how long to wait before ``attempt`` + 1, in seconds.

    A server that named a delay gets that delay, capped at
    :data:`MAX_BACKOFF` so one bad header cannot park a caller for an hour.
    Otherwise the wait doubles with each attempt and is drawn from the range
    between half of that and all of it, so callers that failed together do
    not return together.
    """
    if retry_after is not None:
        return min(max(retry_after, 0.0), MAX_BACKOFF)
    doublings = min(max(attempt - 1, 0), 20)
    ceiling = min(0.5 * 2.0**doublings, MAX_BACKOFF)
    return ceiling * (0.5 + 0.5 * random.random())
