# Payments API incident response

## Declaring an incident

Page the payments on-call through the incident channel and post the customer impact in the first message.

## Ledger checks

Before failing over, check ledger-db replication lag. Fail over only if lag stays above 30 seconds for five minutes.

## Communication

Post updates every 30 minutes until the incident is resolved. The incident commander owns the timeline.
