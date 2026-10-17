"""Runtime configuration from environment variables. Defaults match the dev Compose stack."""
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DATABASE_URL = _env("KL_DATABASE_URL", "postgresql://kl_app:app-dev-only@postgres:5432/kl")
# Each process's pool of database connections (H1). Every process's maximum, added up, stays under Postgres's
# max_connections (100 by default). The budget (M3): the API's KL_API_WORKERS processes (2) at 10 each, the MCP server's
# 10, each web server's 10 (KL_WEB_DB_POOL_MAX), the worker's few, OpenFGA's datastore, and room for provisioning, smoke,
# and an operator. More API processes, or more replicas, means a smaller pool each, or a larger max_connections.
DB_POOL_MIN = int(_env("KL_DB_POOL_MIN", "1"))
DB_POOL_MAX = int(_env("KL_DB_POOL_MAX", "10"))
DB_POOL_TIMEOUT = float(_env("KL_DB_POOL_TIMEOUT", "10"))  # seconds a request waits for a connection before a 503
SERVICE_NAME = _env("OTEL_SERVICE_NAME", "kl-api")  # what its connections are called in pg_stat_activity, with its pid
CHANGE_FEED_DAYS = int(_env("KL_CHANGE_FEED_DAYS", "90"))  # how far back the change feed reaches (P3)
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
# Notion API addresses a Notion connection may use (S6).
NOTION_API_BASES = [b.strip().rstrip("/") for b in _env("KL_NOTION_API_BASES", "https://api.notion.com/v1").split(",") if b.strip()]
# Slack Web API addresses a Slack connection may use (S4).
SLACK_API_BASES = [b.strip().rstrip("/") for b in _env("KL_SLACK_API_BASES", "https://slack.com/api").split(",") if b.strip()]
# Microsoft Graph addresses an Entra directory may use (S3); its tokens come from login.microsoftonline.com.
MICROSOFT_GRAPH_BASES = [b.strip().rstrip("/") for b in _env("KL_MICROSOFT_GRAPH_BASES", "https://graph.microsoft.com").split(",")
                         if b.strip()]
# Data catalogs a catalog connection may use (S7). Dataplex: Google's own APIs (the sentinel https://googleapis.com
# means dataplex., bigquery., and datalineage.googleapis.com), or the dev fake. Unity Catalog: Databricks workspaces
# (https://<workspace>.cloud.databricks.com, .azuredatabricks.net, .gcp.databricks.com, always allowed) and these. Glue:
# AWS's own regional endpoints (always allowed) and these. Purview: Purview accounts (https://<account>.purview.azure.com,
# always allowed) and these. DataHub: only these, since each company runs its own (none unless set).
DATAPLEX_API_BASES = [b.strip().rstrip("/") for b in _env("KL_DATAPLEX_API_BASES", "https://googleapis.com").split(",") if b.strip()]
DATABRICKS_API_BASES = [b.strip().rstrip("/") for b in _env("KL_DATABRICKS_API_BASES", "").split(",") if b.strip()]
AWS_API_BASES = [b.strip().rstrip("/") for b in _env("KL_AWS_API_BASES", "").split(",") if b.strip()]
PURVIEW_API_BASES = [b.strip().rstrip("/") for b in _env("KL_PURVIEW_API_BASES", "").split(",") if b.strip()]
DATAHUB_API_BASES = [b.strip().rstrip("/") for b in _env("KL_DATAHUB_API_BASES", "").split(",") if b.strip()]
# Where the runner's own identity token comes from for AWS (S7, Glue: AssumeRoleWithWebIdentity): a token file when the
# platform mounts one (EKS, AWS_WEB_IDENTITY_TOKEN_FILE), otherwise the GCP metadata server's identity token.
AWS_WEB_IDENTITY_TOKEN_FILE = _env("AWS_WEB_IDENTITY_TOKEN_FILE", "")
# Credential references to the company's own secret stores (G3, ADR 0012), each read as the runner itself. HashiCorp
# Vault: its one address, and the JWT auth role (and mount, and the audience of the runner's identity token) it logs in
# with. AWS Secrets Manager: the regions references may name, and the role the runner assumes. Azure Key Vault: the
# vaults references may name, and the Entra app the runner federates into (no secret). Each is off unless set.
HCVAULT_ADDR = _env("KL_HCVAULT_ADDR", "").rstrip("/")
HCVAULT_ROLE = _env("KL_HCVAULT_ROLE", "aerugo")
HCVAULT_AUTH_PATH = _env("KL_HCVAULT_AUTH_PATH", "jwt")
HCVAULT_AUDIENCE = _env("KL_HCVAULT_AUDIENCE", "vault")
AWS_SECRET_REGIONS = [r.strip() for r in _env("KL_AWS_SECRET_REGIONS", "").split(",") if r.strip()]
AWS_SECRETS_ROLE = _env("KL_AWS_SECRETS_ROLE", "")
AWS_SECRETS_API_BASE = _env("KL_AWS_SECRETS_API_BASE", "")  # AWS's own endpoints; the dev fake in Compose
AZURE_KEY_VAULTS = [v.strip() for v in _env("KL_AZURE_KEY_VAULTS", "").split(",") if v.strip()]
AZURE_TENANT_ID = _env("KL_AZURE_TENANT_ID", "")
AZURE_CLIENT_ID = _env("KL_AZURE_CLIENT_ID", "")
AZURE_LOGIN_BASE = _env("KL_AZURE_LOGIN_BASE", "https://login.microsoftonline.com")
AZURE_KEY_VAULT_BASE = _env("KL_AZURE_KEY_VAULT_BASE", "https://{vault}.vault.azure.net")
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
