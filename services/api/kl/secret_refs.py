"""Credential references to the company's own secret stores (G3, ADR 0012), beside OpenBao's and GCP Secret Manager's:

- HashiCorp Vault: hcvault://<mount>/kl/<org>/<team>/<name>, a KV v2 secret on the one Vault the deployment names
  (KL_HCVAULT_ADDR), read after logging in with the runner's own identity token (Vault's JWT auth, KL_HCVAULT_ROLE).
- AWS Secrets Manager: awssm://<region>/kl_<org>_<team>_<name>, in a region the deployment allows, read as the role the
  runner assumes with its identity token (KL_AWS_SECRETS_ROLE), every call SigV4-signed.
- Azure Key Vault: azkv://<vault>/kl--<org>--<team>--<name>, in a vault the deployment allows, read with an Entra
  token the runner gets by workload identity federation (its identity token as the client assertion; no secret).

Each is fenced to its team by its name, as GCP's are: a team's admin can only set a reference to the team's own secrets
(a team whose slug holds "--" can't use Key Vault, whose names allow only dashes), and every read re-checks that the
deployment still allows the store and place. Each secret's value is a JSON object,
the fields a connection would otherwise have been given. Nothing here is ever logged."""
import json
import re

import httpx

from . import config

VAULT_REF = re.compile(r"^hcvault://([a-z0-9][a-z0-9_-]{0,63})/kl/([a-z0-9-]{1,40})/([a-z0-9][a-z0-9-]{0,39})/([a-z0-9][a-z0-9/_-]{0,150})$")
AWS_REF = re.compile(r"^awssm://([a-z]{2}(?:-gov)?-[a-z]+-[1-9])/(kl_[a-z0-9-]{1,40}_[a-z0-9][a-z0-9-]{0,39}_[A-Za-z0-9_-]{1,150})$")
AZURE_REF = re.compile(r"^azkv://([a-z0-9][a-z0-9-]{1,22}[a-z0-9])/(kl--[a-z0-9-]{1,40}--[a-z0-9][a-z0-9-]{0,39}--[A-Za-z0-9-]{1,100})$")
SCHEMES = ("hcvault://", "awssm://", "azkv://")


def allowed(ref: str | None) -> bool:
    """Whether a reference names a store and place the deployment allows now, whichever team it's for."""
    ref = ref or ""
    if (m := VAULT_REF.fullmatch(ref)):
        return bool(config.HCVAULT_ADDR) and m[2] == config.ORG_SLUG
    if (m := AWS_REF.fullmatch(ref)):
        return m[1] in config.AWS_SECRET_REGIONS and bool(config.AWS_SECRETS_ROLE) and m[2].startswith(f"kl_{config.ORG_SLUG}_")
    if (m := AZURE_REF.fullmatch(ref)):
        return m[1] in config.AZURE_KEY_VAULTS and m[2].startswith(f"kl--{config.ORG_SLUG}--")
    return False


def team_owns(org: str, ws: str, ref: str | None) -> bool:
    """Whether a reference names a secret this team keeps, in a store and place the deployment allows."""
    ref = ref or ""
    if (m := VAULT_REF.fullmatch(ref)):
        return bool(config.HCVAULT_ADDR) and m[2] == org and m[3] == ws
    if (m := AWS_REF.fullmatch(ref)):
        prefix = f"kl_{org}_{ws}_"
        return m[1] in config.AWS_SECRET_REGIONS and bool(config.AWS_SECRETS_ROLE) and m[2].startswith(prefix) and len(m[2]) > len(prefix)
    if (m := AZURE_REF.fullmatch(ref)):
        prefix = f"kl--{org}--{ws}--"
        return "--" not in ws and m[1] in config.AZURE_KEY_VAULTS and m[2].startswith(prefix) and len(m[2]) > len(prefix) \
            and "--" not in m[2][len(prefix):]
    return False


def _object(value) -> dict:
    data = json.loads(value) if isinstance(value, str) else value
    if not isinstance(data, dict):
        raise ValueError("a secret Aerugo reads holds a JSON object")
    return data


def _vault(m: re.Match) -> dict:
    from .glue import web_identity_token
    base = config.HCVAULT_ADDR.rstrip("/")
    login = httpx.post(f"{base}/v1/auth/{config.HCVAULT_AUTH_PATH}/login", timeout=10,
                       json={"role": config.HCVAULT_ROLE, "jwt": web_identity_token(config.HCVAULT_AUDIENCE)})
    login.raise_for_status()
    token = login.json()["auth"]["client_token"]
    path = "/".join(("kl", m[2], m[3], m[4]))
    r = httpx.get(f"{base}/v1/{m[1]}/data/{path}", headers={"X-Vault-Token": token}, timeout=10)
    r.raise_for_status()
    return _object(r.json()["data"]["data"])


def _aws(m: re.Match) -> dict:
    from .glue import AwsRole
    reader = AwsRole(config.AWS_SECRETS_API_BASE, m[1], config.AWS_SECRETS_ROLE, "aerugo-secrets")
    got = reader._call("secretsmanager", "/", {"SecretId": m[2]},
                       {"content-type": "application/x-amz-json-1.1", "x-amz-target": "secretsmanager.GetSecretValue"})
    return _object(got.get("SecretString") or "")


def _azure(m: re.Match) -> dict:
    from .glue import web_identity_token
    token = httpx.post(f"{config.AZURE_LOGIN_BASE.rstrip('/')}/{config.AZURE_TENANT_ID}/oauth2/v2.0/token", timeout=15, data={
        "grant_type": "client_credentials", "client_id": config.AZURE_CLIENT_ID, "scope": "https://vault.azure.net/.default",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": web_identity_token("api://AzureADTokenExchange")})
    token.raise_for_status()
    base = config.AZURE_KEY_VAULT_BASE.replace("{vault}", m[1]).rstrip("/")
    r = httpx.get(f"{base}/secrets/{m[2]}", params={"api-version": "7.4"},
                  headers={"Authorization": f"Bearer {token.json()['access_token']}"}, timeout=10)
    r.raise_for_status()
    return _object(r.json()["value"])


def get(ref: str) -> dict:
    """The secret a reference names, as the JSON object it holds. Which team's it is was checked when the reference was
    set (connections_api.team_secret, as for OpenBao's and GCP's); here, at the moment of use, the deployment's
    allowlists are checked again, so a store, region, or vault the deployment no longer allows is read no more."""
    if not allowed(ref):
        raise ValueError("that secret store isn't allowed here")
    for pattern, read in ((VAULT_REF, _vault), (AWS_REF, _aws), (AZURE_REF, _azure)):
        if (m := pattern.fullmatch(ref or "")):
            return read(m)
    raise ValueError("unsupported credential reference")
