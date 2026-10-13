"""Runtime configuration from environment variables. Defaults match the dev Compose stack."""
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DATABASE_URL = _env("KL_DATABASE_URL", "postgresql://kl_app:app-dev-only@postgres:5432/kl")
ORG_SLUG = _env("KL_ORG_SLUG", "acme")
OPENFGA_URL = _env("KL_OPENFGA_URL", "http://openfga:8080").rstrip("/")
OPENFGA_STORE = _env("KL_OPENFGA_STORE", "kl-dev")
OPENBAO_URL = _env("KL_OPENBAO_URL", "http://openbao:8200").rstrip("/")
OPENBAO_TOKEN = _env("KL_OPENBAO_TOKEN")
OIDC_ISSUER = _env("KL_OIDC_ISSUER", "http://localhost:8081/realms/acme")
OIDC_JWKS_URL = _env("KL_OIDC_JWKS_URL", "http://keycloak:8080/realms/acme/protocol/openid-connect/certs")
OIDC_AUDIENCE = _env("KL_OIDC_AUDIENCE", "kl-api")
# CI issuers whose workload tokens may push to a team's registered pipelines (slice J1, ADR 0007). Signing keys are
# fetched only from these, so a token can't point the API at another server.
PIPELINE_ISSUERS = [i.strip().rstrip("/") for i in _env("KL_PIPELINE_ISSUERS", "https://token.actions.githubusercontent.com").split(",")
                    if i.strip()]
PIPELINE_AUDIENCE = "kl-ingest"
# Google API addresses a Drive connection may use (slice F2, ADR 0008); a connection can name no other server.
GOOGLE_API_BASES = [b.strip().rstrip("/") for b in _env("KL_GOOGLE_API_BASES", "https://www.googleapis.com").split(",") if b.strip()]
