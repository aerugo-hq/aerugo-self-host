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
# Google Admin API addresses a Workspace directory may use (S2): the Directory API is on its own host, not Drive's.
GOOGLE_ADMIN_API_BASES = [b.strip().rstrip("/") for b in _env("KL_GOOGLE_ADMIN_API_BASES", "https://admin.googleapis.com").split(",")
                          if b.strip()]
# Microsoft Graph addresses an Entra directory may use (S3); its tokens come from login.microsoftonline.com.
MICROSOFT_GRAPH_BASES = [b.strip().rstrip("/") for b in _env("KL_MICROSOFT_GRAPH_BASES", "https://graph.microsoft.com").split(",")
                         if b.strip()]
# Jira addresses a connection may use besides Jira Cloud sites (https://<site>.atlassian.net, always allowed): in dev,
# the fake one smoke runs. Anything else is refused, so a team admin can't point the worker at an internal host.
JIRA_API_BASES = [b.strip().rstrip("/") for b in _env("KL_JIRA_API_BASES", "").split(",") if b.strip()]
# Okta addresses a directory connection may use besides Okta orgs (https://<org>.okta.com, always allowed): in dev, the
# fake one smoke runs.
OKTA_API_BASES = [b.strip().rstrip("/") for b in _env("KL_OKTA_API_BASES", "").split(",") if b.strip()]
# Confluence sites a connection may use beyond Confluence Cloud's own (https://<site>.atlassian.net, always allowed):
# in dev, the fake one smoke runs.
CONFLUENCE_API_BASES = [b.strip().rstrip("/") for b in _env("KL_CONFLUENCE_API_BASES", "").split(",") if b.strip()]
# GCP Secret Manager credential references (ADR 0011): the projects a reference may name (none unless set), and where
# Google's APIs are (in dev, the fake ones smoke runs). On GCP the runner signs in as its own workload identity, through
# the metadata server; elsewhere with a service account key file (GOOGLE_APPLICATION_CREDENTIALS).
GCP_SECRET_PROJECTS = [p.strip() for p in _env("KL_GCP_SECRET_PROJECTS", "").split(",") if p.strip()]
GCP_SECRETS_BASE = _env("KL_GCP_SECRETS_BASE", "https://secretmanager.googleapis.com").rstrip("/")
GCP_METADATA_BASE = _env("KL_GCP_METADATA_BASE", "http://metadata.google.internal").rstrip("/")
GCP_TOKEN_URL = _env("KL_GCP_TOKEN_URL", "https://oauth2.googleapis.com/token")
GCP_CREDENTIALS_FILE = _env("GOOGLE_APPLICATION_CREDENTIALS", "")
# The email domains the company declares as its own (ADR 0011). Identity matching links a person to source accounts by
# their SSO-verified email only within these; empty means no automatic matching at all.
ORG_EMAIL_DOMAINS = [d.strip().lower() for d in _env("KL_ORG_EMAIL_DOMAINS", "").split(",") if d.strip()]
