# Data catalogs

What a company sets up for Aerugo to read its data catalogs, and exactly the access each one needs. Aerugo reads
**metadata only, never data**: names, descriptions, columns, owners, tags, glossary terms, and lineage. It never runs a
query against a table.

## What a catalog becomes

Each catalog entry (a table, a view, a dataset) becomes two things:

- **A document** with its description, its columns as a table, its facts (the system, tags, glossary terms, where the
  data lives), and its owners. Only the people the catalog lets read the table find it, mirrored from the catalog's
  own access control. When Aerugo can't tie a grant to a person (a group it can't expand, a service account, a role),
  that grant reaches no one. An entry nobody in Aerugo may read isn't a document at all.
- **An asset** for the connecting team, with its lineage and its owners, as pushed lineage is (K3): the team's
  members see it on the Map and in impact walks (`GET /v1/lineage/impact`), and the catalog's owners show in who to
  ask about a page that declares the asset's name among its aliases. Other teams see none of it.

A sync reads everything again, so a grant taken away in the catalog takes the document away on the next sync, and an
entry gone from the catalog leaves.

## Dataplex Universal Catalog (Google Cloud)

**Keyless.** Aerugo reads Dataplex as the runner's own identity: on GCP, the workload identity of the service the
runner runs as; elsewhere, the runner's own key file (`GOOGLE_APPLICATION_CREDENTIALS`). No key is pasted anywhere.
Because every team shares the runner's identity, **only an org admin connects Dataplex**.

Grant the runner's service account, on each project to index:

- **Dataplex Catalog Viewer** (`roles/dataplex.catalogViewer`): to search entries and read their aspects.
- **BigQuery Metadata Viewer** (`roles/bigquery.metadataViewer`): to read a dataset's access list and a table's IAM
  policy, which is how Aerugo knows who may read each table. It reads no rows.
- **Data Lineage Viewer** (`roles/datalineage.viewer`): to read lineage.

**Who reads an entry:** for a BigQuery table, the people its dataset's access list (readers, writers, owners) and
its IAM policy (data viewers, editors, owners, admins) name, as their Google accounts. A Google group reaches its
members once an org admin connects Google Workspace as a directory (S2). The company's domain reaches the whole org
only when the connection names that domain, and it must be one of the company's own. Project-level grants, special
groups (project readers), service accounts, `allUsers`, `allAuthenticatedUsers`, and metadata-only roles reach no
one. An entry from any other system has no permissions Aerugo can mirror, so it's an asset for the team and not a
document.

## Unity Catalog (Databricks)

Create a **service principal** in the workspace with an **OAuth secret** (machine-to-machine). Give Aerugo its
application id and the secret (kept in OpenBao, or a secret the team keeps holding `{"client_secret": "..."}`).

- **`USE CATALOG`, `USE SCHEMA`, and `BROWSE`** on each catalog to index, to list its schemas and tables and read
  their metadata.
- **Reading a table's grants** (the effective permissions) needs the service principal to own the catalog, hold
  `MANAGE` on it, or be a metastore admin: Databricks has no read-only privilege for grants. Choose `MANAGE` on only
  the catalogs to index. Aerugo never changes a grant.

**Who reads a table:** the people its effective permissions let `SELECT` it (or hold `ALL PRIVILEGES`), directly or
inherited from its schema or catalog, as their Databricks accounts, matched to Aerugo's people by verified email.
Groups reach no one (a service principal can't list their members), and neither do `BROWSE` and other privileges that
don't read data.

## AWS Glue Data Catalog (and SageMaker Catalog)

**Keyless.** The runner assumes an IAM role the company creates for it, with its own identity token
(`AssumeRoleWithWebIdentity`): no AWS access key exists anywhere. **Only an org admin connects Glue.** SageMaker
Catalog's assets come from this same Glue Data Catalog, so this connector reads them; SageMaker's business glossary
isn't read.

The role's trust policy trusts the runner's identity:

- **On GCP:** the federated principal `accounts.google.com`, for `sts:AssumeRoleWithWebIdentity`, with the
  conditions `accounts.google.com:sub` equal to the runner's service account's unique id and
  `accounts.google.com:oaud` equal to `sts.amazonaws.com`.
- **On EKS:** the cluster's OIDC provider, for the runner's Kubernetes service account (`AWS_WEB_IDENTITY_TOKEN_FILE`
  is mounted for it).

The role's permissions: `glue:GetDatabases` and `glue:GetTables`, and `lakeformation:ListPermissions`. Make the role
a Lake Formation **read-only administrator**, so it can list every grant without being able to change one.

**Who reads a table:** the people Lake Formation grants `SELECT` (or `ALL`) on the table, through the company's SAML
identity provider (`arn:aws:iam::<account>:saml-provider/<provider>:user/<email>`), as their AWS accounts. IAM users
and roles aren't people, IAM Identity Center users aren't named by email, `IAMAllowedPrincipals` (IAM-only access
control) can't be resolved to people, and a grant on the database, or `DESCRIBE` alone, doesn't read the table: all
of these reach no one. Glue records no lineage.

## Microsoft Purview

Register an app in Entra with a **certificate** (never a client secret), as for Microsoft 365. Give Aerugo the tenant
id, the app's id, the Purview account's address (`https://<account>.purview.azure.com`), and the collections to index,
by id.

- In the Purview portal, add the app to each collection's **Data readers**, to search its assets and read their
  details and lineage.
- To know who may read a collection, Aerugo reads its metadata policy. If Purview refuses that, the sync fails with
  Purview's answer rather than guessing.

**Who reads an asset:** the people and Entra groups its collection's own policy names as data readers or data
curators. Purview names them by Entra object id, so a person counts once an org admin has connected Entra as a
directory (S3), and a group reaches its members through it. What a collection inherits from its parent isn't
followed, so the readers are only ever fewer than Purview's. Collection admins, and anyone the directory doesn't list,
reach no one.

## DataHub

Each company runs its own DataHub, so its address must be one the deployment allows (`KL_DATAHUB_API_BASES`). Create a
DataHub user for Aerugo, give it a policy that views metadata only, and make an **access token** for it (kept in
OpenBao, or a secret the team keeps holding `{"token": "..."}`). Name the platforms whose datasets to read, as DataHub
names them (`snowflake`, `bigquery`).

**Who reads a dataset:** DataHub decides who may view metadata through its policies, which a token can't read, so a
dataset is readable only by **the people DataHub names as its owners**, as their DataHub accounts. A group owner
reaches no one. Everyone else who may view it in DataHub just doesn't find it in Aerugo.

## dbt and Dataform manifests, from CI

A CI pipeline (J1) pushes a project's manifest: `POST /v1/workspaces/<team>/manifests`, signed in with its own
workload token, with `kind` (`dbt` for `target/manifest.json`, `dataform` for the compiled graph), `project` (names
the project's documents, so each project replaces only its own), `readers` (each holding a role on the team), and
`manifest`. Each model, seed, snapshot, source, or table becomes a document the named readers find, and an asset with
lineage from what it depends on and its owners (`meta.owner`, or `meta.owners`). A model gone from the next manifest
leaves. A manifest carries no permissions, so its readers are the ones the pipeline names, as for any pushed document.

## What changes in place

Cadence, pausing, and each catalog's list (projects, catalogs, databases, collections, platforms) change in place; a
secret Aerugo stored can be rotated (Unity Catalog's OAuth secret, Purview's certificate, DataHub's token). Anything
else (the catalog, its address, the role, the region) means connecting again.
