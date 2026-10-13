-- Slice T2: a team's admins can remove a connection. Its row goes at once (its containers cascade), so nothing syncs from
-- it again; the worker then retires what it brought in (connection.retire), as a removed pipeline's documents go.
-- org_isolation on connections has no FOR clause, so it already scopes this delete to the caller's org.
SET search_path = kl, public;
GRANT DELETE ON connections TO kl_app;
