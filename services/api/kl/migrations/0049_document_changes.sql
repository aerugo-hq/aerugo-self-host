-- A3: the change feed. Every document added, changed (its content or its readers), or removed, in order, with the
-- readers it had at that moment, so the feed shows each person only the changes to what they could read (RLS, as
-- documents themselves are read), and a removed document only to those who could read it before.
SET search_path = kl, public;

CREATE TABLE document_changes (
  id             bigserial PRIMARY KEY,
  org_id         uuid NOT NULL REFERENCES orgs(id),
  external_key   text NOT NULL,
  change         text NOT NULL CHECK (change IN ('added', 'changed', 'removed')),
  source_kind    text,
  acl_principals text[] NOT NULL,
  at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX document_changes_org ON document_changes (org_id, id);
ALTER TABLE document_changes ENABLE ROW LEVEL SECURITY;
CREATE POLICY read_by_acl ON document_changes FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND acl_principals && kl.current_principals());
CREATE POLICY written_in_org ON document_changes FOR INSERT TO kl_app, kl_ingest WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT ON document_changes TO kl_app;
GRANT INSERT ON document_changes TO kl_ingest;
GRANT USAGE ON SEQUENCE document_changes_id_seq TO kl_app, kl_ingest;

CREATE FUNCTION kl.note_document_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO kl.document_changes (org_id, external_key, change, source_kind, acl_principals)
      VALUES (OLD.org_id, OLD.external_key, 'removed', OLD.source_kind, OLD.acl_principals);
    RETURN OLD;
  ELSIF TG_OP = 'INSERT' THEN
    INSERT INTO kl.document_changes (org_id, external_key, change, source_kind, acl_principals)
      VALUES (NEW.org_id, NEW.external_key, 'added', NEW.source_kind, NEW.acl_principals);
  ELSIF NEW.content_hash IS DISTINCT FROM OLD.content_hash OR NEW.acl_principals IS DISTINCT FROM OLD.acl_principals THEN
    -- a change of readers reaches everyone who could read it before or can now: those who lost it see it go
    INSERT INTO kl.document_changes (org_id, external_key, change, source_kind, acl_principals)
      VALUES (NEW.org_id, NEW.external_key, 'changed', NEW.source_kind,
              ARRAY(SELECT DISTINCT unnest(NEW.acl_principals || OLD.acl_principals)));
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER documents_changes AFTER INSERT OR UPDATE OR DELETE ON documents
  FOR EACH ROW EXECUTE FUNCTION kl.note_document_change();
