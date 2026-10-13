-- Slice R2: the pipeline view. A team's admins may put a failed run back in the queue. The app role gets only the
-- columns a retry resets; the API decides whose run it is and whether it can run again.
SET search_path = kl, public;

GRANT UPDATE (status, attempts, run_after, last_error, locked_at, updated_at) ON jobs TO kl_app;
