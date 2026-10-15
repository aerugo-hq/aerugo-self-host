-- Spaces by name (the Documents tree): each container a connection discovers keeps the name its source gives it (a
-- Confluence space, a Jira project, a shared drive or folder, a repository), so its documents group under that name
-- instead of a web address. Discovery rewrites a connection's containers each time, so names stay current.
SET search_path = kl, public;

ALTER TABLE connection_containers ADD COLUMN IF NOT EXISTS display_name text
  CHECK (display_name IS NULL OR length(display_name) <= 300);
