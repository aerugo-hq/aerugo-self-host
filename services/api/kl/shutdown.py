"""A worker asked to stop (SIGTERM, as a deploy sends) stops at the next safe point of the job it's running, and the
worker puts the job back in the queue (C1). A safe point is between the items or containers of a sync: each item's rows
are written in a transaction of their own, and a sync run again converges, so nothing is left half done."""
import threading
from collections.abc import Iterable, Iterator

requested = threading.Event()


class Stopped(BaseException):
    """The worker is stopping. A BaseException, so no `except Exception` on the way out treats it as the job's failure."""


def each(items: Iterable) -> Iterator:
    """The items of a long loop, stopping at a safe point before each one once a stop is requested."""
    for item in items:
        if requested.is_set():
            raise Stopped()
        yield item
