"""Credential references to Google Cloud Secret Manager (ADR 0011): a team keeps a connection's secret in its own GCP
project, and Aerugo holds only its address, gcpsm://projects/<project>/secrets/<secret>[/versions/<version>].

The runner reads it as itself: on GCP, as the workload identity of the service it runs as (the metadata server), so
no key exists anywhere; elsewhere, with a service account key (GOOGLE_APPLICATION_CREDENTIALS). The secret's value is
a JSON object, the fields a connection would otherwise have been given (an email and api_token, a service_account).

A reference must name a project the deployment allows (KL_GCP_SECRET_PROJECTS; none, unless it says) and a secret
named for its team, kl_<org>_<team>_<name>. Teams' names hold hyphens but never underscores, so one team's prefix can't
be another's (payments can't claim payments-x's secrets)."""
import base64
import json
import re
import time

import httpx
import jwt

from . import config

SCHEME = "gcpsm://"
REF = re.compile(r"^gcpsm://projects/([a-z][a-z0-9-]{4,28}[a-z0-9])/secrets/([A-Za-z0-9_-]{1,255})(?:/versions/(latest|[1-9][0-9]{0,9}))?$")
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_token = {"value": None, "expires": 0.0}


def parse(ref: str | None) -> tuple[str, str, str] | None:
    """A reference's project, secret, and version (latest unless it names one); None if it isn't one."""
    m = REF.fullmatch(ref or "")
    return (m[1], m[2], m[3] or "latest") if m else None


def team_owns(org: str, ws: str, ref: str | None) -> bool:
    """Whether a reference names a secret this team keeps: in a project the deployment allows, named kl_<org>_<team>_<name>."""
    parts = parse(ref)
    if not parts:
        return False
    project, secret, _ = parts
    prefix = f"kl_{org}_{ws}_"
    return project in config.GCP_SECRET_PROJECTS and secret.startswith(prefix) and len(secret) > len(prefix)


def _access_token() -> str:
    if _token["value"] and _token["expires"] > time.time() + 60:
        return _token["value"]
    if config.GCP_CREDENTIALS_FILE:  # a service account key, off GCP
        with open(config.GCP_CREDENTIALS_FILE) as f:
            key = json.load(f)
        now = int(time.time())
        assertion = jwt.encode({"iss": key["client_email"], "scope": SCOPE, "aud": config.GCP_TOKEN_URL, "iat": now,
                                "exp": now + 3600}, key["private_key"], algorithm="RS256")
        r = httpx.post(config.GCP_TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                   "assertion": assertion}, timeout=15)
    else:  # on GCP: the workload identity of the service the runner runs as
        r = httpx.get(f"{config.GCP_METADATA_BASE}/computeMetadata/v1/instance/service-accounts/default/token",
                      headers={"Metadata-Flavor": "Google"}, timeout=5)
    r.raise_for_status()
    body = r.json()
    _token.update(value=body["access_token"], expires=time.time() + int(body.get("expires_in", 300)))
    return _token["value"]


def get(ref: str) -> dict:
    """The secret a reference names, as the JSON object it holds."""
    parts = parse(ref)
    if not parts:
        raise ValueError("unsupported credential reference")
    project, secret, version = parts
    r = httpx.get(f"{config.GCP_SECRETS_BASE}/v1/projects/{project}/secrets/{secret}/versions/{version}:access",
                  headers={"Authorization": f"Bearer {_access_token()}"}, timeout=10)
    r.raise_for_status()
    value = json.loads(base64.b64decode(r.json()["payload"]["data"]))
    if not isinstance(value, dict):
        raise ValueError("a secret Aerugo reads holds a JSON object")
    return value
