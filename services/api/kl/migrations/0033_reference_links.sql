-- Slice K2 (ADR 0010): recorded links. A document that writes down another document's address or one of its
-- identifiers (a URL, a ticket key like EDP-412, a table name like ORDER_HIST, a dotted name like edp.orders) links
-- to it. Recorded links are facts a source wrote, so each carries its provenance (invariant 14): the signal that made
-- it, how strong that signal is, and the text that made it.
-- Visibility is the semantic edges' rule (slice N): for the app role, a reference is readable only when both of its
-- documents are, under the documents' own RLS, so this holds for any query, not only the map. Everything cascades
-- from documents, so a team's purge or a connection's removal takes its references with it.
SET search_path = kl, public;

CREATE TABLE reference_links (
  org_id      uuid NOT NULL REFERENCES orgs(id),
  source_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,  -- the document that wrote the reference
  target_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,  -- the document it points at
  signal      text NOT NULL CHECK (signal IN ('url', 'identifier')),
  strength    real NOT NULL CHECK (strength > 0 AND strength <= 1),
  evidence    text NOT NULL CHECK (length(evidence) BETWEEN 1 AND 200),   -- what the source wrote: the address or name
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, target_id, signal),
  CHECK (source_id <> target_id)
);
CREATE INDEX reference_links_target_idx ON reference_links (target_id);

CREATE TABLE reference_scans (            -- which document version, against which set of identifiers, was last scanned
  document_id  uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  org_id       uuid NOT NULL REFERENCES orgs(id),
  content_hash text NOT NULL,
  names_hash   text NOT NULL,             -- a new page declaring an identifier rescans everything once
  scanned_at   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE reference_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference_scans ENABLE ROW LEVEL SECURITY;

-- The worker computes everything, inside its org.
CREATE POLICY ingest_in_org ON reference_links FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON reference_scans FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

-- The app reads a reference only when the caller can read both of its documents.
CREATE POLICY read_if_both_visible ON reference_links FOR SELECT TO kl_app USING (
  org_id = kl.current_org()
  AND EXISTS (SELECT 1 FROM documents d WHERE d.id = source_id)
  AND EXISTS (SELECT 1 FROM documents d WHERE d.id = target_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON reference_links, reference_scans TO kl_ingest;
GRANT SELECT ON reference_links TO kl_app;
