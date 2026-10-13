-- Slice S: admins can change or remove a team's models. The API checks admin rights; RLS keeps every
-- change inside the org. Usage history outlives the model it was recorded against.
SET search_path = kl, public;

GRANT UPDATE, DELETE ON model_endpoints TO kl_app;
CREATE POLICY admin_update_in_org ON model_endpoints FOR UPDATE TO kl_app
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY admin_delete_in_org ON model_endpoints FOR DELETE TO kl_app
  USING (org_id = kl.current_org());

-- Replace usage's foreign key to its model (whatever it was named) with one that keeps the usage row.
DO $$
DECLARE fk text;
BEGIN
  FOR fk IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'kl.model_usage'::regclass AND contype = 'f'
              AND confrelid = 'kl.model_endpoints'::regclass
  LOOP
    EXECUTE format('ALTER TABLE kl.model_usage DROP CONSTRAINT %I', fk);
  END LOOP;
END $$;
ALTER TABLE model_usage ALTER COLUMN endpoint_id DROP NOT NULL;
ALTER TABLE model_usage ADD CONSTRAINT model_usage_endpoint_fk
  FOREIGN KEY (endpoint_id) REFERENCES model_endpoints(id) ON DELETE SET NULL;
