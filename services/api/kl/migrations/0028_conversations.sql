-- Slice R1: conversations. A person's questions and answers, kept so they can follow up and come back to them.
-- They're private to their owner, enforced here by row-level security, so an org admin can't read them either; an
-- answer is re-checked against the reader's current access every time it's read. Data-subject requests (slice I1)
-- cover them explicitly, and erasure deletes them rather than relabelling them: they're the person's own words.
SET search_path = kl, public;

CREATE TABLE conversations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL REFERENCES orgs(id),
  owner      text NOT NULL,
  title      text NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_by_owner ON conversations (org_id, owner, updated_at DESC);

CREATE TABLE conversation_messages (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES orgs(id),
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('user', 'assistant')),
  content         text NOT NULL,
  citations       jsonb NOT NULL DEFAULT '[]',
  model           text,
  created_at      timestamptz NOT NULL DEFAULT clock_timestamp()  -- a question and its answer, in order
);
CREATE INDEX conversation_messages_in_order ON conversation_messages (conversation_id, created_at);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY own ON conversations TO kl_app
  USING (org_id = kl.current_org() AND 'user:' || owner = ANY (kl.current_principals()))
  WITH CHECK (org_id = kl.current_org() AND 'user:' || owner = ANY (kl.current_principals()));
-- A message is visible only through a conversation the caller can see, so the owner rule above governs both.
CREATE POLICY own ON conversation_messages TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM conversations c WHERE c.id = conversation_id))
  WITH CHECK (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM conversations c WHERE c.id = conversation_id));
GRANT SELECT, INSERT, DELETE ON conversations TO kl_app;
GRANT UPDATE (title, updated_at) ON conversations TO kl_app;
GRANT SELECT, INSERT ON conversation_messages TO kl_app;

-- A person's conversations for their export. Owner-privileged, inside the caller's org only, because an org admin
-- answering the person's request can't read them otherwise.
CREATE FUNCTION kl.subject_conversations(who text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
           'title', c.title, 'started', c.created_at,
           'messages', (SELECT COALESCE(jsonb_agg(jsonb_build_object('role', m.role, 'content', m.content, 'at', m.created_at)
                                                  ORDER BY m.created_at), '[]')
                          FROM conversation_messages m WHERE m.conversation_id = c.id))
         ORDER BY c.created_at), '[]')
  FROM conversations c
  WHERE kl.current_org() IS NOT NULL AND c.org_id = kl.current_org() AND c.owner = who
$$;

-- Erasure deletes a person's conversations outright: they're the person's own words, so nothing is kept.
CREATE FUNCTION kl.subject_erase_conversations(who text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  n bigint;
BEGIN
  IF kl.current_org() IS NULL THEN
    RETURN NULL;
  END IF;
  DELETE FROM conversations WHERE org_id = kl.current_org() AND owner = who;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

REVOKE ALL ON FUNCTION kl.subject_conversations(text), kl.subject_erase_conversations(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.subject_conversations(text), kl.subject_erase_conversations(text) TO kl_app;
