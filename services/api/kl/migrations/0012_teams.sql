-- Slice K1: teams are created and renamed through the API instead of SQL. The API checks that the caller
-- is an org admin (to create) or a team admin (to change); the org-isolation policy keeps every write
-- inside the caller's org.
SET search_path = kl, public;

GRANT INSERT, UPDATE ON workspaces TO kl_app;
