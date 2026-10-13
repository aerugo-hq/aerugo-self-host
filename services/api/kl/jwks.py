"""Verifying tokens against an issuer's published signing keys: Keycloak's for people (auth.py), CI issuers' for
pipelines (slice J1), and Google's for identity links (slice F2b).

The issuer's key set is cached for five minutes, and no single key is cached beyond it, so a key the issuer withdraws
(after a leak, say) stops working within five minutes rather than at the next restart. A signature that fails against
the cached set is retried once against a fresh one, for an issuer that has just rotated a key; so that forged tokens
can't turn that retry into a flood of requests to the issuer, each key set refreshes early at most once a minute.
"""
import threading
import time

import jwt
from jwt import PyJWKClient

LIFESPAN = 300      # seconds an issuer's key set is trusted before it's fetched again
REFRESH_EVERY = 60  # at most one early refresh per key set in this window
_last_refresh: dict[int, float] = {}
_lock = threading.Lock()


def client(uri: str, lifespan: int = LIFESPAN) -> PyJWKClient:
    return PyJWKClient(uri, cache_keys=False, cache_jwk_set=True, lifespan=lifespan)


def _may_refresh(keys: PyJWKClient) -> bool:
    now = time.monotonic()
    with _lock:
        if now - _last_refresh.get(id(keys), float("-inf")) < REFRESH_EVERY:
            return False
        _last_refresh[id(keys)] = now
        return True


def decode(keys: PyJWKClient, token: str, **kwargs) -> dict:
    """jwt.decode with the signing key from the issuer's current key set. It raises what jwt.decode raises."""
    try:
        return jwt.decode(token, keys.get_signing_key_from_jwt(token).key, **kwargs)
    except jwt.InvalidSignatureError:
        if not _may_refresh(keys):
            raise
        keys.get_jwk_set(refresh=True)
        return jwt.decode(token, keys.get_signing_key_from_jwt(token).key, **kwargs)
