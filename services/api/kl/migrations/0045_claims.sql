-- Q3: claim-level checking. Each answer keeps its claims with it (hidden with it, like its citations), and each org
-- keeps the threshold its evaluation set calibrated, which every answer's claims are checked against.
SET search_path = kl, public;

ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS claims jsonb;

CREATE TABLE claim_calibration (
  org_id         uuid PRIMARY KEY REFERENCES orgs(id),
  threshold      real NOT NULL CHECK (threshold > 0 AND threshold <= 1),
  accuracy       real NOT NULL CHECK (accuracy BETWEEN 0 AND 1),
  samples        int  NOT NULL CHECK (samples > 0),
  golden_version text NOT NULL,
  calibrated_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE claim_calibration ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON claim_calibration USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT ON claim_calibration TO kl_app;
