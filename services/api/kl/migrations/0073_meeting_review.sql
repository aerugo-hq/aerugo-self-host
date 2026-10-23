-- C3b (docs/ENTERPRISE_PLAN.md section 6): a meeting's people confirm what it proposed in Teams.
--
-- When a meeting's proposals are ready, its organizer gets one private review card in their 1:1 chat with the bot. The
-- bot reaches someone in Teams by their Teams id, so a captured meeting keeps its organizer's, read back from the Bot
-- Connector when they turned capture on (the person with that id in the meeting's chat is the organizer), never a
-- delivery's word. Nothing else is new: cards and their buttons are jobs, and settling a unit is migration 0072's rule.
SET search_path = kl, public;

ALTER TABLE connection_containers ADD COLUMN organizer_ref text;  -- a captured meeting's organizer, by their Teams id
