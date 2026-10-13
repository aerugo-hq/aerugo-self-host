"""OpenFGA client: the authorization engine behind the policy decision point."""
import httpx

from . import config


class OpenFGA:
    def __init__(self, base_url: str, store_name: str):
        self.base = base_url
        self.store_name = store_name
        self._store_id: str | None = None

    def store_id(self) -> str:
        if self._store_id is None:
            r = httpx.get(f"{self.base}/stores", timeout=5)
            r.raise_for_status()
            match = [s["id"] for s in r.json().get("stores", []) if s.get("name") == self.store_name]
            if not match:
                raise RuntimeError(f"OpenFGA store {self.store_name!r} not found; did the provision step run?")
            self._store_id = match[0]
        return self._store_id

    def check(self, user: str, relation: str, obj: str) -> bool:
        r = httpx.post(
            f"{self.base}/stores/{self.store_id()}/check",
            json={"tuple_key": {"user": user, "relation": relation, "object": obj}},
            timeout=5,
        )
        r.raise_for_status()
        return bool(r.json().get("allowed"))

    def list_objects(self, user: str, relation: str, type_: str) -> list[str]:
        r = httpx.post(
            f"{self.base}/stores/{self.store_id()}/list-objects",
            json={"type": type_, "relation": relation, "user": user},
            timeout=5,
        )
        r.raise_for_status()
        return sorted(r.json().get("objects", []))

    def batch_check(self, user: str, relation: str, objects: list[str]) -> set[str]:
        """The given objects the user holds the relation on, 50 checks per request (OpenFGA's default batch
        limit). Used where list-objects would be capped, so nothing is silently missing."""
        allowed: set[str] = set()
        for start in range(0, len(objects), 50):
            chunk = objects[start:start + 50]
            r = httpx.post(f"{self.base}/stores/{self.store_id()}/batch-check", timeout=10, json={"checks": [
                {"tuple_key": {"user": user, "relation": relation, "object": obj}, "correlation_id": str(n)}
                for n, obj in enumerate(chunk)]})
            r.raise_for_status()
            allowed |= {chunk[int(cid)] for cid, res in r.json().get("result", {}).items() if res.get("allowed")}
        return allowed

    def read(self, tuple_key: dict) -> list[dict]:
        """All tuples matching a (partial) key, following continuation tokens."""
        found, token = [], ""
        while True:
            body = {"tuple_key": tuple_key, "page_size": 100}
            if token:
                body["continuation_token"] = token
            r = httpx.post(f"{self.base}/stores/{self.store_id()}/read", json=body, timeout=10)
            r.raise_for_status()
            data = r.json()
            found += [t["key"] for t in data.get("tuples", [])]
            token = data.get("continuation_token") or ""
            if not token:
                return found

    def write(self, writes=(), deletes=()) -> None:
        """Write and delete (user, relation, object) tuples in batches under the API's per-request limit."""
        for section, tuples in (("writes", writes), ("deletes", deletes)):
            keys = [{"user": u, "relation": r, "object": o} for u, r, o in sorted(tuples)]
            for i in range(0, len(keys), 50):
                resp = httpx.post(f"{self.base}/stores/{self.store_id()}/write",
                                  json={section: {"tuple_keys": keys[i:i + 50]}}, timeout=10)
                if resp.status_code >= 400:
                    raise RuntimeError(f"OpenFGA rejected {section} ({resp.status_code}): {resp.text[:300]}")


fga = OpenFGA(config.OPENFGA_URL, config.OPENFGA_STORE)
