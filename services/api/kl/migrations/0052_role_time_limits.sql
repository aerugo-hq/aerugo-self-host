-- H1: time limits per role, so a runaway query or a transaction left open can't hold a pooled connection forever.
-- The API (kl_app) answers people: 30 seconds a statement, a minute idle inside a transaction (no request waits on a
-- model inside one: H5). The worker (kl_ingest) syncs, embeds, and purges: ten minutes each. They apply to each new
-- session of the role; a deployment that needs others sets its own with ALTER ROLE.
ALTER ROLE kl_app    SET statement_timeout = '30s';
ALTER ROLE kl_app    SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE kl_ingest SET statement_timeout = '10min';
ALTER ROLE kl_ingest SET idle_in_transaction_session_timeout = '10min';
