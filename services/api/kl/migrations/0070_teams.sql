-- C1a (docs/ENTERPRISE_PLAN.md section 6): Microsoft Teams channels as a source, with capture.
--
-- A `teams` connection reads the standard channels of the teams whose owners consented to the company's Teams app
-- (resource-specific consent), each post with its replies a document readable by the team's own members. A channel's
-- container keeps its team's id in default_ref (Graph addresses a channel through its team). Capture uses C2b's
-- columns: the owner's consent is the first key, so a consented team's channels arrive as requested, and the team's
-- admin in Aerugo confirms each. No new table: "don't capture" marks are capture_exclusions, by the message's id.
SET search_path = kl, public;

ALTER TABLE connections DROP CONSTRAINT IF EXISTS connections_kind_check;
ALTER TABLE connections ADD CONSTRAINT connections_kind_check
  CHECK (kind IN ('github', 'google', 'jira', 'confluence', 'slack', 'microsoft', 'notion', 'directory', 'catalog', 'teams'));
