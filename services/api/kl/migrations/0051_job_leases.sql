-- C1: a running job holds a lease. The worker running it renews the lease while it lives; once a lease runs out (the
-- worker was killed, ran out of memory, or crashed), the scheduler's reaper puts the job back in the queue, counting the
-- attempt. Without it, a job a dead worker left running stayed running forever, and since a connection's sync is only
-- scheduled when none of its jobs is pending, that connection never synced again: no new content, no revocations.
SET search_path = kl, public;

ALTER TABLE jobs ADD COLUMN lease_until timestamptz;  -- NULL until a worker renews it; the reaper then counts from locked_at
CREATE INDEX jobs_running_idx ON jobs (lease_until) WHERE status = 'running';
