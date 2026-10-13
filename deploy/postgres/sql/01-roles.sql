-- Least-privilege roles.
--   kl_owner  (POSTGRES_USER) owns the schema and runs migrations. Never used by services.
--   kl_app    serves reads and admin writes. Always subject to row-level security.
--   kl_ingest worker role: writes content inside its org. Always subject to row-level security.
--   kl_fga    owns the separate OpenFGA datastore database.
CREATE ROLE kl_app    LOGIN PASSWORD :'app_password'    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE kl_ingest LOGIN PASSWORD :'ingest_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE kl_fga    LOGIN PASSWORD :'fga_password'    NOSUPERUSER NOCREATEDB NOCREATEROLE;

CREATE DATABASE openfga OWNER kl_fga;

REVOKE ALL ON DATABASE kl FROM PUBLIC;
GRANT CONNECT ON DATABASE kl TO kl_app, kl_ingest;
REVOKE ALL ON DATABASE openfga FROM PUBLIC;
