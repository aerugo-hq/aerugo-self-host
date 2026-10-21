-- C2d (docs/ENTERPRISE_PLAN.md section 6): the bot answers `/aerugo ask` and mentions privately, as the asker.
--
-- person_sign_ins: each person's last verified sign-in (a token the API or the MCP server checked), with the groups it
-- named. A Slack ask runs as the person its Slack account is linked to, with these groups, only while this is recent
-- (KL_SLACK_ASK_DAYS, 7): their groups and admission are only as current as their last sign-in. Written by the API at
-- most every five minutes per person (admission.sync_memberships), never by an ask. The username is among the names a
-- data-subject erasure finds (kl.subject_columns finds every `username`).
-- jobs.lease_until: the answers lane (kl.answers_lane) claims `slack.ask` jobs as the API's own role, so row-level
-- security applies to an ask exactly as to the API's; it may already set a job's status, and now its lease (C1).
SET search_path = kl, public;

CREATE TABLE person_sign_ins (
  org_id       uuid NOT NULL REFERENCES orgs(id),
  username     text NOT NULL CHECK (length(username) BETWEEN 1 AND 200),
  groups       text[] NOT NULL DEFAULT '{}',
  signed_in_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, username)
);
ALTER TABLE person_sign_ins ENABLE ROW LEVEL SECURITY;
CREATE POLICY app_in_org ON person_sign_ins FOR ALL TO kl_app USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON person_sign_ins FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE ON person_sign_ins TO kl_app;
GRANT SELECT, UPDATE, DELETE ON person_sign_ins TO kl_ingest;

GRANT UPDATE (lease_until) ON jobs TO kl_app;
