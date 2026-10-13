"""OIDC bearer-token authentication: signature via JWKS, plus issuer, audience, and expiry. People sign in through the
web app or a CLI; a registered service account (slice T1) signs in as itself, with the client-credentials flow."""
import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from . import config, jwks
from .principals import is_valid

log = logging.getLogger("kl.auth")
_jwks = jwks.client(config.OIDC_JWKS_URL)  # the issuer's key set, never a key cached past it (jwks.py)
SERVICE_PREFIX = "service-account-"  # Keycloak's name for the user behind a client's own token


@dataclass(frozen=True)
class Caller:
    username: str
    groups: tuple[str, ...]
    org_slug: str
    service: bool = False  # a registered service account acting as itself (slice T1)

    @property
    def fga_user(self) -> str:
        return f"user:{self.username}"

    @property
    def principals(self) -> list[str]:
        if self.service:  # its own grants and nothing org-wide: an automation account reads only what it's given
            candidates = [f"user:{self.username}"]
        else:
            candidates = [f"user:{self.username}", f"org:{self.org_slug}", *(f"group:{g}" for g in self.groups)]
        return [p for p in candidates if is_valid(p)]


def service_client(claims: dict) -> str | None:
    """The client a token was issued to on its own behalf (the client-credentials flow), by Keycloak's convention: its
    user is "service-account-<client>", and that client is the token's authorized party (azp)."""
    username, client = claims.get("preferred_username"), claims.get("azp")
    if isinstance(username, str) and isinstance(client, str) and client and username == f"{SERVICE_PREFIX}{client}":
        return client
    return None


def caller_from_claims(claims: dict, org_slug: str, allow_service: bool = False) -> Caller:
    """Who a validated token speaks for. A service account's token is refused unless the caller allows them (the REST API
    does, then checks the account is registered; the MCP server doesn't, since assistants act for people). And a person
    can never hold a name that looks like a service account's, so nobody can inherit one's roles."""
    username = claims.get("preferred_username")
    if not isinstance(username, str) or not is_valid(f"user:{username}"):
        raise HTTPException(401, "token has no usable username")
    if service_client(claims):
        if not allow_service:
            raise HTTPException(401, "service accounts can't sign in here")
        return Caller(username=username, groups=(), org_slug=org_slug, service=True)
    if username.startswith(SERVICE_PREFIX):
        raise HTTPException(401, "that username is reserved for service accounts")
    groups = set()
    for raw in claims.get("groups") or []:
        name = str(raw).strip("/")
        if is_valid(f"group:{name}"):
            groups.add(name)
        else:
            log.warning("dropping group with unsafe name from token: %r", raw)
    return Caller(username=username, groups=tuple(sorted(groups)), org_slug=org_slug)


def decode_token(token: str, audience: str | None = None) -> dict:
    """Validate a bearer token for one audience: kl-api for the REST API, kl-mcp for the MCP server."""
    return jwks.decode(
        _jwks,
        token,
        algorithms=["RS256"],  # pinned: never let the token choose its algorithm
        audience=audience or config.OIDC_AUDIENCE,
        issuer=config.OIDC_ISSUER,
        leeway=30,
        options={"require": ["exp", "iat", "iss", "aud"]},
    )


def authenticate(authorization: str | None = Header(default=None)) -> Caller:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_token(authorization.split(" ", 1)[1].strip())
    except (InvalidTokenError, PyJWKClientError) as e:
        log.info("token rejected: %s", type(e).__name__)
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
    caller = caller_from_claims(claims, config.ORG_SLUG, allow_service=True)
    if caller.service and not registered(service_client(claims)):
        raise HTTPException(401, "this service account isn't registered, or is disabled", headers={"WWW-Authenticate": "Bearer"})
    return caller


def registered(client: str | None) -> bool:
    """Whether an org admin registered this client as a service account and it's enabled. Asked on every request, so
    disabling or removing one takes effect on its very next call."""
    from .db import connect  # here, not at import time, so the MCP server needn't reach the database to authenticate
    try:
        with connect() as conn:
            row = conn.execute("SELECT kl.service_account_enabled(%s, %s) AS ok", (config.ORG_SLUG, client)).fetchone()
        return bool(row and row["ok"])
    except Exception:  # noqa: BLE001 - if we can't tell, the answer is no
        log.warning("couldn't check whether service account %s is registered", client)
        return False
