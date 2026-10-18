"""The services' OpenBao token (M6): signed in for with the platform's identity, reused for two thirds of its life,
signed in for again when it's refused, and never replaced by anything else when signing in fails."""
import httpx
import pytest

from kl import vault as vault_module
from kl.vault import SecretStore

URL = "http://openbao:8200"


class OpenBao:
    """A stand-in for OpenBao: sign-ins hand out numbered tokens an hour long; `revoked` tokens are refused."""

    def __init__(self, sign_in_status: int = 200):
        self.sign_ins, self.calls, self.revoked, self.sign_in_status = [], [], set(), sign_in_status

    def post(self, url, json=None, **_):
        assert url == f"{URL}/v1/auth/kubernetes/login"
        self.sign_ins.append(json)
        body = {"auth": {"client_token": f"token-{len(self.sign_ins)}", "lease_duration": 3600}}
        return httpx.Response(self.sign_in_status, json=body if self.sign_in_status == 200 else {},
                              request=httpx.Request("POST", url))

    def request(self, method, url, headers=None, **_):
        token = headers["X-Vault-Token"]
        self.calls.append(token)
        status = 403 if token in self.revoked else 200
        return httpx.Response(status, json={"data": {"data": {"api_key": "k"}}}, request=httpx.Request(method, url))


@pytest.fixture
def openbao(monkeypatch):
    def make(sign_in_status: int = 200) -> OpenBao:
        fake = OpenBao(sign_in_status)
        monkeypatch.setattr(vault_module.httpx, "post", fake.post)
        monkeypatch.setattr(vault_module.httpx, "request", fake.request)
        monkeypatch.setattr(vault_module, "platform_identity", lambda: "the-pods-service-account-token")
        return fake
    return make


def signed_in_store() -> SecretStore:
    return SecretStore(URL, "", login=("kubernetes", "aerugo-services"))


def test_without_a_platform_identity_the_static_token_is_used_as_it_is(openbao):
    fake = openbao()
    SecretStore(URL, "dev-services-token").get("openbao://secret/kl/acme/payments/models/m")
    assert fake.calls == ["dev-services-token"] and fake.sign_ins == []


def test_a_service_signs_in_with_its_identity_once_and_reuses_the_token(openbao):
    fake = openbao()
    store = signed_in_store()
    for _ in range(3):
        store.get("openbao://secret/kl/acme/payments/models/m")
    assert fake.sign_ins == [{"role": "aerugo-services", "jwt": "the-pods-service-account-token"}]
    assert fake.calls == ["token-1"] * 3


def test_a_token_two_thirds_through_its_life_is_signed_in_for_again(openbao, monkeypatch):
    fake = openbao()
    now = [1000.0]
    monkeypatch.setattr(vault_module.time, "monotonic", lambda: now[0])
    store = signed_in_store()
    store.get("openbao://secret/kl/acme/payments/models/m")
    now[0] += 3600 * 2 / 3 + 1
    store.get("openbao://secret/kl/acme/payments/models/m")
    assert fake.calls == ["token-1", "token-2"] and len(fake.sign_ins) == 2


def test_a_refused_token_is_signed_in_for_again_once_and_the_call_retried(openbao):
    fake = openbao()
    store = signed_in_store()
    store.get("openbao://secret/kl/acme/payments/models/m")
    fake.revoked.add("token-1")
    assert store.get("openbao://secret/kl/acme/payments/models/m") == {"api_key": "k"}
    assert fake.calls == ["token-1", "token-1", "token-2"]


def test_a_refused_sign_in_fails_closed_with_no_other_token_tried(openbao):
    fake = openbao(sign_in_status=403)
    store = SecretStore(URL, "a-static-token-that-must-not-be-used", login=("kubernetes", "aerugo-services"))
    with pytest.raises(httpx.HTTPStatusError):
        store.get("openbao://secret/kl/acme/payments/models/m")
    assert fake.calls == []


def test_an_auth_path_that_isnt_a_plain_name_is_refused():
    for bad in ("../sys", "kubernetes/login", "", "Kubernetes"):
        with pytest.raises(ValueError):
            SecretStore(URL, "", login=(bad, "aerugo-services"))
