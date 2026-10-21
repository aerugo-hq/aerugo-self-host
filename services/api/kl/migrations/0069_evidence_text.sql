-- F6, evidence by its text: a confirmed unit goes back for review when the text Aerugo keeps from a piece of its
-- evidence changes, not when only the source's version moves (a synced document's content_hash is its source's
-- version, and Jira's moved for every issue with pull request 76, sending back every unit citing one with the same
-- text). kl.evidence_fingerprints says what a unit's evidence snapshot holds, one entry per document in the evidence's
-- order: the SHA-256 of the text it keeps (each section's heading and body, in order) when it's indexed; its version
-- when it keeps no text (a pointer or live only, F11, or an indexed document with no sections); '' for one that's gone.
-- It runs as its caller, so the API's confirmation reads it under the caller's row-level security (they read every
-- piece of the unit's evidence) and the worker within its org. Nothing here widens who reads anything.
SET search_path = kl, public;

CREATE FUNCTION kl.evidence_fingerprints(ids uuid[]) RETURNS text[] LANGUAGE sql STABLE AS $$
  SELECT ARRAY(
    SELECT coalesce(CASE WHEN d.storage = 'index' THEN (
             SELECT encode(sha256(convert_to(jsonb_agg(jsonb_build_array(c.heading_path, c.body) ORDER BY c.ordinal)::text, 'UTF8')), 'hex')
             FROM kl.chunks c WHERE c.document_id = d.id) END, d.content_hash, '')
    FROM unnest(ids) WITH ORDINALITY AS e(doc, n) LEFT JOIN kl.documents d ON d.id = e.doc ORDER BY e.n)
$$;

-- Units confirmed before now hold their documents' versions. One whose versions still match (its evidence unchanged
-- since it was confirmed, as far as versions tell) takes its documents' fingerprints now, so an upgrade that reads every
-- document again under a new version sends none of them back; one whose versions already moved keeps them, and goes
-- stale at the next pass, as it would have. A unit already stale stays so, for its owners to confirm.
UPDATE knowledge_units u SET evidence_hashes = kl.evidence_fingerprints(u.evidence)
WHERE u.status = 'confirmed' AND u.evidence_hashes = ARRAY(
  SELECT coalesce(d.content_hash, '') FROM unnest(u.evidence) WITH ORDINALITY AS e(doc, n)
  LEFT JOIN documents d ON d.id = e.doc ORDER BY e.n);
