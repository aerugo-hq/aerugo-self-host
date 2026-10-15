-- S3: a directory group by its own id as well as its email. An Entra security group often has no email, and Microsoft
-- 365 shares a file with a group by its object id, so each group's rows carry the provider's id for it (Google's
-- groups are found by email and leave it empty). A group without an email is keyed by its id in group_email too.
SET search_path = kl, public;

ALTER TABLE directory_groups ADD COLUMN IF NOT EXISTS group_id text CHECK (group_id IS NULL OR length(group_id) <= 200);
CREATE INDEX IF NOT EXISTS directory_groups_id_idx ON directory_groups (org_id, group_id) WHERE group_id IS NOT NULL;
