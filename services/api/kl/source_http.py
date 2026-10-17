"""Calls to a source's API, for every connector (H2): one throttled call never fails a whole sync.

A 429 or a 503, or a source's own sign of throttling (`throttled`: Google's rate-limit 403, Graph's 504, AWS's
ThrottlingException, GitHub's exhausted limit), is waited out and tried again, up to RETRIES times: as long as the
source's Retry-After says (seconds, or an HTTP date), else exponential backoff with jitter, capped at MAX_BACKOFF. A
source asking for longer than MAX_WAIT gets its answer back at once, so the job fails and its own retry comes back
later instead of holding the worker. A wait ends early when the worker is asked to stop (C1): the job goes back in
the queue. Calls keep their own timeouts and follow no redirects, as before.

A connection's calls are already one at a time: its jobs never overlap (the scheduler skips a connection with one
pending, and a lease keeps one running), and each job calls its source in turn. ponytail: a per-connection
semaphore here once a sync fetches in parallel."""
import logging
import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from . import shutdown

log = logging.getLogger("kl.source_http")
RETRIES = 5
MAX_BACKOFF = 30.0  # seconds, for a source that doesn't say how long
MAX_WAIT = 60.0     # the longest a source may ask a call to wait
THROTTLED = (429, 503)


def wait(r: httpx.Response, attempt: int) -> float | None:
    """How long to wait before trying again, or None when the source asks for longer than MAX_WAIT."""
    after = r.headers.get("Retry-After", "").strip()
    seconds = None
    if after.replace(".", "", 1).isdigit():
        seconds = float(after)
    elif after:
        try:
            seconds = max(0.0, parsedate_to_datetime(after).timestamp() - time.time())
        except (TypeError, ValueError):
            seconds = None
    if seconds is None:
        return min(MAX_BACKOFF, 2.0 ** attempt) + random.random()
    return seconds if seconds <= MAX_WAIT else None


def send(method: str, url: str, *, throttled: Callable[[httpx.Response], bool] | None = None, **kwargs) -> httpx.Response:
    """One call to a source (httpx.request's arguments), tried again while it's throttled. Returns the last answer,
    for the caller to judge as before."""
    attempt = 0
    while True:
        r = httpx.request(method, url, **kwargs)
        if attempt == RETRIES or not (r.status_code in THROTTLED or (throttled is not None and throttled(r))):
            return r
        seconds = wait(r, attempt)
        if seconds is None:
            log.warning("%s asked for a wait longer than %ss; giving its answer back", urlsplit(url).hostname, MAX_WAIT)
            return r
        log.info("throttled by %s (%s); trying again in %.1fs", urlsplit(url).hostname, r.status_code, seconds)
        if shutdown.requested.wait(seconds):  # stopping: the job goes back in the queue (C1)
            raise shutdown.Stopped()
        attempt += 1


def get(url: str, **kwargs) -> httpx.Response:
    return send("GET", url, **kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    return send("POST", url, **kwargs)


if __name__ == "__main__":
    assert wait(httpx.Response(429, headers={"Retry-After": "2"}), 0) == 2.0
    assert wait(httpx.Response(429, headers={"Retry-After": "3600"}), 0) is None  # longer than a worker waits
    assert 1.0 <= wait(httpx.Response(503), 0) < 2.0 and 30.0 <= wait(httpx.Response(503), 9) < 31.0  # capped backoff
    assert wait(httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), 0) == 0.0  # a date passed
    print("source_http: ok")
