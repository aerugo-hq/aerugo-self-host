-- M1 slice 3: webhook deliveries, recorded once each so a replayed delivery is ignored.
SET search_path = kl, public;

CREATE TABLE webhook_deliveries (
  org_id      uuid NOT NULL REFERENCES orgs(id),
  source      text NOT NULL,                -- github
  delivery_id text NOT NULL,                -- X-GitHub-Delivery
  event       text NOT NULL,
  queued_jobs int  NOT NULL DEFAULT 0,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, source, delivery_id)
);
ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON webhook_deliveries USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE ON webhook_deliveries TO kl_app;
