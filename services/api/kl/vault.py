"""Secrets backend (OpenBao KV v2, Vault-compatible API). The platform stores only the
reference this returns; the secret itself never reaches Postgres, logs, or traces."""
import base64

import httpx

from . import config

_SCHEME = "openbao://"


class SecretStore:
    def __init__(self, url: str, token: str, mount: str = "secret"):
        self.url, self.token, self.mount = url, token, mount

    def _headers(self) -> dict:
        return {"X-Vault-Token": self.token}

    def put(self, path: str, data: dict) -> str:
        r = httpx.post(f"{self.url}/v1/{self.mount}/data/{path}", json={"data": data},
                       headers=self._headers(), timeout=5)
        r.raise_for_status()
        return f"{_SCHEME}{self.mount}/{path}"

    def get(self, ref: str) -> dict:
        if ref.startswith("gcpsm://"):  # a secret a team keeps in GCP Secret Manager (ADR 0011)
            from . import gcp_secrets
            return gcp_secrets.get(ref)
        prefix = f"{_SCHEME}{self.mount}/"
        if not ref.startswith(prefix):
            raise ValueError("unsupported credential reference")
        r = httpx.get(f"{self.url}/v1/{self.mount}/data/{ref[len(prefix):]}",
                      headers=self._headers(), timeout=5)
        r.raise_for_status()
        return r.json()["data"]["data"]

    def destroy(self, ref: str) -> None:
        """Delete a secret with every version it ever had (KV v2 metadata delete), not just the latest.
        Deleting one that's already gone is fine, so a retried purge doesn't fail here."""
        prefix = f"{_SCHEME}{self.mount}/"
        if not ref.startswith(prefix):
            raise ValueError("unsupported credential reference")
        r = httpx.delete(f"{self.url}/v1/{self.mount}/metadata/{ref[len(prefix):]}",
                         headers=self._headers(), timeout=5)
        if r.status_code != 404:
            r.raise_for_status()

    def rotate(self, ref: str, data: dict) -> None:
        """Write a new version of a secret, then destroy every older version, so an old value is gone for
        good, not just superseded."""
        prefix = f"{_SCHEME}{self.mount}/"
        if not ref.startswith(prefix):
            raise ValueError("unsupported credential reference")
        path = ref[len(prefix):]
        r = httpx.post(f"{self.url}/v1/{self.mount}/data/{path}", json={"data": data}, headers=self._headers(), timeout=5)
        r.raise_for_status()
        older = list(range(1, int(r.json()["data"]["version"])))
        if older:
            r = httpx.post(f"{self.url}/v1/{self.mount}/destroy/{path}", json={"versions": older},
                           headers=self._headers(), timeout=5)
            r.raise_for_status()

    # ---- Transit (slice I3): encryption keys that never leave OpenBao. Destroying a key shreds everything
    # encrypted with it, backups included.
    def transit_key(self, name: str) -> None:
        """Create a key if it doesn't exist (creating an existing one changes nothing), deletable when its
        team is purged."""
        r = httpx.post(f"{self.url}/v1/transit/keys/{name}", json={"type": "aes256-gcm96"}, headers=self._headers(), timeout=5)
        r.raise_for_status()
        r = httpx.post(f"{self.url}/v1/transit/keys/{name}/config", json={"deletion_allowed": True},
                       headers=self._headers(), timeout=5)
        r.raise_for_status()

    def encrypt(self, name: str, text: str) -> str:
        r = httpx.post(f"{self.url}/v1/transit/encrypt/{name}",
                       json={"plaintext": base64.b64encode(text.encode()).decode()}, headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()["data"]["ciphertext"]

    def decrypt_many(self, name: str, ciphertexts: list[str]) -> list[str]:
        """Decrypt a batch under one key, in one call."""
        if not ciphertexts:
            return []
        r = httpx.post(f"{self.url}/v1/transit/decrypt/{name}",
                       json={"batch_input": [{"ciphertext": c} for c in ciphertexts]}, headers=self._headers(), timeout=30)
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
        r = httpx.get(f"{self.url}/v1/transit/keys/{name}", headers=self._headers(), timeout=5)
        if r.status_code == 404:
            return
        r.raise_for_status()
        r = httpx.delete(f"{self.url}/v1/transit/keys/{name}", headers=self._headers(), timeout=5)
        if r.status_code == 400 and "not found" in r.text.lower():
            return
        r.raise_for_status()

    def storage(self) -> str:
        """The storage backend in use: "file" for the persistent dev server, "inmem" for `server -dev`."""
        return httpx.get(f"{self.url}/v1/sys/seal-status", timeout=5).json().get("storage_type", "unknown")

    def health(self) -> dict:
        r = httpx.get(f"{self.url}/v1/sys/health", timeout=5)
        return r.json()


vault = SecretStore(config.OPENBAO_URL, config.OPENBAO_TOKEN)
