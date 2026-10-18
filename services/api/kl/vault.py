"""Secrets backend (OpenBao KV v2, Vault-compatible API). The platform stores only the
reference this returns; the secret itself never reaches Postgres, logs, or traces.

The services' own OpenBao token (M6): in a deployment, signed in for with the platform's identity (a Kubernetes
service account, or the GCP VM's), an hour at most and signed in for again before then, so nothing long-lived is
stored; in dev and CI, a static token scoped to their paths (G1)."""
import base64
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager

import httpx

from . import config

_SCHEME = "openbao://"
log = logging.getLogger("kl.vault")


AUTH_PATH = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


class SecretStore:
    def __init__(self, url: str, token: str, mount: str = "secret", login: tuple[str, str] | None = None):
        """`login`: the auth method's path and role to sign in with (M6); without one, `token` is used as it is."""
        if login and not AUTH_PATH.fullmatch(login[0]):
            raise ValueError("KL_OPENBAO_AUTH_PATH is an auth method's path: lowercase letters, digits, and dashes")
        self.url, self.token, self.mount, self.login = url, token, mount, login
        self._lock = threading.Lock()
        self._signed_in: tuple[str, float] | None = None  # the token signed in for, and when to sign in again

    def headers(self) -> dict:
        """The services' token as OpenBao takes it, signed in for when it's due if the platform's identity is used."""
        if not self.login:
            return {"X-Vault-Token": self.token}
        with self._lock:
            if self._signed_in is None or time.monotonic() >= self._signed_in[1]:
                self._signed_in = self._sign_in()
            return {"X-Vault-Token": self._signed_in[0]}

    def _sign_in(self) -> tuple[str, float]:
        """Sign in with the platform's identity token; a refusal raises (deny by default, never a fallback). The token
        is used for two thirds of its life. Neither token is ever logged."""
        path, role = self.login
        r = httpx.post(f"{self.url}/v1/auth/{path}/login", json={"role": role, "jwt": platform_identity()}, timeout=10)
        r.raise_for_status()
        auth = r.json()["auth"]
        lease = float(auth.get("lease_duration") or 0)
        return auth["client_token"], time.monotonic() + lease * 2 / 3 if lease else math.inf

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        """One call to OpenBao as the services. A token refused (revoked, or past its life) is signed in for again once."""
        r = httpx.request(method, f"{self.url}/v1/{path}", headers=self.headers(), **kw)
        if r.status_code == 403 and self.login:
            with self._lock:
                self._signed_in = None
            r = httpx.request(method, f"{self.url}/v1/{path}", headers=self.headers(), **kw)
        return r

    def ref(self, path: str) -> str:
        """The reference a secret written at this path has, so a row can name it before it's written."""
        return f"{_SCHEME}{self.mount}/{path}"

    def put(self, path: str, data: dict) -> str:
        r = self._call("POST", f"{self.mount}/data/{path}", json={"data": data}, timeout=5)
        r.raise_for_status()
        return self.ref(path)

    @contextmanager
    def kept_if_committed(self) -> Generator[Callable[[str, dict], str], None, None]:
        """Secrets a change writes before it has committed (H5). Enter it before the change's transaction,
        `with vault.kept_if_committed() as put, scoped(...) as conn:`, and every secret written with `put` is destroyed
        again if the change fails, so a failed change never leaves one behind. Only for a path no other change writes
        at the same time: a fresh one, or one whose row the change has already claimed."""
        written: list[str] = []

        def put(path: str, data: dict) -> str:
            ref = self.put(path, data)
            written.append(ref)
            return ref

        try:
            yield put
        except BaseException:
            for ref in written:
                try:
                    self.destroy(ref)
                except (httpx.HTTPError, ValueError) as e:  # the change still failed; say what it left behind
                    log.error("a secret written for a change that failed is still in the backend (%s): %s", type(e).__name__, ref)
            raise

    def destroy_committed(self, ref: str | None) -> None:
        """Destroy the secret a committed change removed (H5: only after the commit, or a change that then rolled back
        would keep a row naming a secret that's gone). A failure leaves it orphaned, which is logged, not raised: the
        change itself is done. ponytail: an outbox the worker retries, if orphans turn up in practice."""
        if not ref:
            return
        try:
            self.destroy(ref)
        except (httpx.HTTPError, ValueError) as e:
            log.error("a secret its change removed couldn't be destroyed (%s): %s", type(e).__name__, ref)

    def get(self, ref: str) -> dict:
        if ref.startswith("gcpsm://"):  # a secret a team keeps in GCP Secret Manager (ADR 0011)
            from . import gcp_secrets
            return gcp_secrets.get(ref)
        if ref.startswith(("hcvault://", "awssm://", "azkv://")):  # Vault, AWS, or Azure (G3, ADR 0012)
            from . import secret_refs
            return secret_refs.get(ref)
        prefix = f"{_SCHEME}{self.mount}/"
        if not ref.startswith(prefix):
            raise ValueError("unsupported credential reference")
        r = self._call("GET", f"{self.mount}/data/{ref[len(prefix):]}", timeout=5)
        r.raise_for_status()
        return r.json()["data"]["data"]

    def destroy(self, ref: str) -> None:
        """Delete a secret with every version it ever had (KV v2 metadata delete), not just the latest.
        Deleting one that's already gone is fine, so a retried purge doesn't fail here."""
        prefix = f"{_SCHEME}{self.mount}/"
        if not ref.startswith(prefix):
            raise ValueError("unsupported credential reference")
        r = self._call("DELETE", f"{self.mount}/metadata/{ref[len(prefix):]}", timeout=5)
        if r.status_code != 404:
            r.raise_for_status()

    def rotate(self, ref: str, data: dict) -> None:
        """Write a new version of a secret, then destroy every older version, so an old value is gone for
        good, not just superseded."""
        prefix = f"{_SCHEME}{self.mount}/"
        if not ref.startswith(prefix):
            raise ValueError("unsupported credential reference")
        path = ref[len(prefix):]
        r = self._call("POST", f"{self.mount}/data/{path}", json={"data": data}, timeout=5)
        r.raise_for_status()
        older = list(range(1, int(r.json()["data"]["version"])))
        if older:
            r = self._call("POST", f"{self.mount}/destroy/{path}", json={"versions": older}, timeout=5)
            r.raise_for_status()

    # ---- Transit (slice I3): encryption keys that never leave OpenBao. Destroying a key shreds everything
    # encrypted with it, backups included.
    def transit_key(self, name: str) -> None:
        """Create a key if it doesn't exist (creating an existing one changes nothing), deletable when its
        team is purged."""
        r = self._call("POST", f"transit/keys/{name}", json={"type": "aes256-gcm96"}, timeout=5)
        r.raise_for_status()
        r = self._call("POST", f"transit/keys/{name}/config", json={"deletion_allowed": True}, timeout=5)
        r.raise_for_status()

    def encrypt(self, name: str, text: str) -> str:
        r = self._call("POST", f"transit/encrypt/{name}", json={"plaintext": base64.b64encode(text.encode()).decode()},
                       timeout=10)
        r.raise_for_status()
        return r.json()["data"]["ciphertext"]

    def decrypt_many(self, name: str, ciphertexts: list[str]) -> list[str]:
        """Decrypt a batch under one key, in one call."""
        if not ciphertexts:
            return []
        r = self._call("POST", f"transit/decrypt/{name}", json={"batch_input": [{"ciphertext": c} for c in ciphertexts]},
                       timeout=30)
        r.raise_for_status()
        results = r.json()["data"]["batch_results"]
        if any("error" in item and item["error"] for item in results):
            raise ValueError("a ciphertext didn't decrypt under its key")
        return [base64.b64decode(item["plaintext"]).decode() for item in results]

    def decrypt(self, name: str, ciphertext: str) -> str:
        return self.decrypt_many(name, [ciphertext])[0]

    def destroy_key(self, name: str) -> None:
        """Delete a key and every version of it. What it encrypted can never be read again, wherever a copy is.
        Deleting a key that's already gone is fine, so a retried purge doesn't fail here. (Reading a missing key
        is a 404, but deleting one is a 400 "not found", so check first, and accept that answer from a race.)"""
        r = self._call("GET", f"transit/keys/{name}", timeout=5)
        if r.status_code == 404:
            return
        r.raise_for_status()
        r = self._call("DELETE", f"transit/keys/{name}", timeout=5)
        if r.status_code == 400 and "not found" in r.text.lower():
            return
        r.raise_for_status()

    def storage(self) -> str:
        """The storage backend in use: "file" for the persistent dev server, "inmem" for `server -dev`."""
        return httpx.get(f"{self.url}/v1/sys/seal-status", timeout=5).json().get("storage_type", "unknown")

    def health(self) -> dict:
        r = httpx.get(f"{self.url}/v1/sys/health", timeout=5)
        return r.json()


def platform_identity() -> str:
    """The token proving who this service is: the file Kubernetes mounts in the pod, else the GCP metadata server's
    identity token for OpenBao's audience (glue's, which G3's Vault sign-in uses too)."""
    if config.OPENBAO_JWT_FILE:
        with open(config.OPENBAO_JWT_FILE) as f:
            return f.read().strip()
    from .glue import web_identity_token
    return web_identity_token(config.OPENBAO_AUDIENCE)


vault = SecretStore(config.OPENBAO_URL, config.OPENBAO_TOKEN,
                    login=(config.OPENBAO_AUTH_PATH, config.OPENBAO_ROLE) if config.OPENBAO_AUTH_PATH else None)
