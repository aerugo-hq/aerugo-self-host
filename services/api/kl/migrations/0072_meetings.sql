-- C3a (docs/ENTERPRISE_PLAN.md section 6): Teams meeting transcripts, opted in by each meeting's organizer.
--
-- A captured meeting is a container of its Teams connection (its chat's id; its organizer and online meeting in
-- default_ref), and each transcript a document whose source kind is 'meeting', readable by the meeting chat's members.
-- A unit whose evidence is meeting transcripts alone is readable only by those people (F1), so they confirm or retire
-- it, not the owner team's curators, who may not have been there. The API names no team for them: these policies let
-- someone change a unit, and record its event, only when it cites meeting transcripts alone and every one is readable to
-- them (kl.evidence_readable, as the caller). kl.meeting_evidence runs as the caller too, so it says nothing about a
-- document they can't read. No new table.
SET search_path = kl, public;

CREATE FUNCTION kl.meeting_evidence(evidence uuid[]) RETURNS boolean LANGUAGE sql STABLE SET search_path = kl, public AS $$
  SELECT cardinality(evidence) > 0 AND NOT EXISTS (
    SELECT 1 FROM unnest(evidence) e WHERE NOT EXISTS (SELECT 1 FROM kl.documents d WHERE d.id = e AND d.source_kind = 'meeting'))
$$;
GRANT EXECUTE ON FUNCTION kl.meeting_evidence(uuid[]) TO kl_app, kl_ingest;

CREATE POLICY settle_from_its_meetings ON knowledge_units FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND kl.evidence_readable(evidence) AND kl.meeting_evidence(evidence))
  WITH CHECK (org_id = kl.current_org() AND kl.evidence_readable(evidence) AND kl.meeting_evidence(evidence));

CREATE POLICY record_from_its_meetings ON unit_events FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org()
              AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id AND kl.meeting_evidence(u.evidence)));
