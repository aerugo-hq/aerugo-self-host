-- F6, evidence by its text: a confirmed unit goes back for review when the text Aerugo keeps from a piece of its
-- evidence changes, not when only the source's version moves (a synced document's content_hash is its source's
-- version, and Jira's moved for every issue with pull request 76, sending back every unit citing one with the same
-- text). Beside each unit's snapshot of its documents' versions (evidence_hashes, F6), a confirmation now keeps the
-- hash of each document's text (evidence_texts, from kl.evidence_texts: the SHA-256 of each section's heading and body,
-- in order, for an indexed document; NULL for one that keeps no text, a pointer or live only, F11, or none at all, or
-- one that's gone). The lifecycle pass compares texts where there's text then and now, and versions otherwise, so
-- switching a connection between indexed and pointers reads the same version and sends nothing back. kl.evidence_texts
-- runs as its caller: the API's confirmation under the caller's row-level security (they read every piece of the
-- unit's evidence), the worker within its org. Nothing here widens who reads anything.
SET search_path = kl, public;

ALTER TABLE knowledge_units ADD COLUMN evidence_texts text[];

CREATE FUNCTION kl.evidence_texts(ids uuid[]) RETURNS text[] LANGUAGE sql STABLE AS $$
  SELECT ARRAY(
    SELECT CASE WHEN d.storage = 'index' THEN (
             SELECT encode(sha256(convert_to(jsonb_agg(jsonb_build_array(c.heading_path, c.body) ORDER BY c.ordinal)::text, 'UTF8')), 'hex')
             FROM kl.chunks c WHERE c.document_id = d.id) END
    FROM unnest(ids) WITH ORDINALITY AS e(doc, n) LEFT JOIN kl.documents d ON d.id = e.doc ORDER BY e.n)
$$;

-- A unit confirmed before now has only versions. One whose versions still match (its evidence unchanged since it was
-- confirmed, as far as versions tell) takes its documents' texts now, so an upgrade that reads every document again
-- under a new version sends none of them back; one whose versions already moved keeps none, and goes stale at the
-- next pass, as it would have. A unit already stale stays so, for its owners to confirm.
UPDATE knowledge_units u SET evidence_texts = kl.evidence_texts(u.evidence)
WHERE u.status = 'confirmed' AND u.evidence_hashes = ARRAY(
  SELECT coalesce(d.content_hash, '') FROM unnest(u.evidence) WITH ORDINALITY AS e(doc, n)
  LEFT JOIN documents d ON d.id = e.doc ORDER BY e.n);
