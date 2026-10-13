-- Slice F2b (ADR 0008): Google identities are linked by signing in to Google, as GitHub logins are (slice K2), with
-- the same single-use, user-bound, short-lived sign-in states.
SET search_path = kl, public;

ALTER TABLE identity_link_states DROP CONSTRAINT identity_link_states_provider_check;
ALTER TABLE identity_link_states ADD CONSTRAINT identity_link_states_provider_check CHECK (provider IN ('github', 'google'));
