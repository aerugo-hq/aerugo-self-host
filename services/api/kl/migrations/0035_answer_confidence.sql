-- Slice K9: each saved answer keeps the confidence it was given (a level and its reasons), so a conversation reloads
-- with it. Reading a conversation still re-checks every cited source; an answer the reader can no longer open is hidden
-- along with its confidence, whose reasons could otherwise count sources they may not see.
SET search_path = kl, public;

ALTER TABLE conversation_messages ADD COLUMN confidence jsonb;
