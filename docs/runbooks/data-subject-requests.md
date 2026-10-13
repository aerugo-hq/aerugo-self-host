# Runbook: data-subject requests

Requests from people about their own data: to see it (GDPR Art. 15, 20), to erase it (Art. 17), and the review of documents that look like they hold personal data. Every step here is audited.

## A request to see their data

1. **Confirm who's asking.** They can serve themselves: signed in, `GET /v1/me/export` returns everything this cell holds about them. Otherwise verify their identity the way your policy requires.
2. **For someone else's request, an org admin runs** `GET /v1/users/{username}/export`.
3. **Check it before sending.** It holds their teams and roles, identity links, model use, audit trail, and counts of records attributed to them; everyone else in it reads "another person".
4. **Send it securely** and record the request, date, and delivery in your request log.

Their sign-in account lives in the identity provider (Keycloak); export it there if they ask for it too.

## A request to erase their data

1. **Check legal holds first** (`GET /v1/retention`). If a hold covers records about them, ask counsel before erasing: a hold can override an erasure request.
2. **An org admin erases them**, repeating the username to confirm:
   ```bash
   curl -X POST "$API/v1/users/$NAME/erase" -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" -d "{\"confirm\": \"$NAME\"}"
   ```
   The response names the pseudonym that replaced them and counts what changed. `remaining` must be 0; if the erasure can't get there, nothing is committed and the call fails.
3. **Delete their account in the identity provider.** Until then they could sign in again as a new, empty user.
4. **Record the pseudonym, not the name,** in your request log, with the date. The erasure's own audit event holds only the pseudonym.

What erasure keeps: audit events stay (pseudonymized, without their search queries and questions), because the audit trail is evidence. What it can't reach: free text in which someone else mentioned them in passing, and copies in backups until those expire.

## Reviewing documents that look personal

The worker flags documents that look like they hold personal data (a résumé, an ID or card number, a date of birth, HR material) with a suggested classification. A team's admins review their team's flags; org admins review every team's.

1. `GET /v1/personal-data` lists open flags: the document, its teams, its classification, the suggestion, and which kinds of personal data were found (counts, never the values).
2. Open the document and decide:
   - **Reclassify** (`POST /v1/personal-data/{id}/resolve` with `{"action": "reclassify"}`): the document becomes at least as strict as suggested, at once, and stays that strict through later re-indexing.
   - **Dismiss** (`{"action": "dismiss"}`): nothing changes, and the flag stays dismissed until the document's findings change.
3. If it shouldn't be in the knowledge base at all, remove it at its source; the next sync removes it here.
