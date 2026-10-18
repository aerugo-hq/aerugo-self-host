# Runbook: a pilot, a team at a time

Use this when a company starts with a few teams (say EDW and EDP) and brings in the rest in waves. Everything is
reviewed code in the company's onboarding repository; Aerugo's example is `examples/onboarding` in
`terraform-provider-aerugo`.

## How it works

1. **Teams as code.** Each team is a folder: `teams/<team>/team.yaml` says the team and who is on it, by role (admins,
   curators, members, auditors), each entry a group from the identity provider (`group:<name>`) or, rarely, a person
   (`user:<username>`). `teams/<team>/sources.yaml` says its sources. Joining or leaving the group is the onboarding:
   no pull request per person.
2. **Groups from the sign-in token.** Aerugo reads the `groups` claim of the token Keycloak signs. On each request it
   makes OpenFGA's group membership match the token, adding the groups joined and removing the groups left, so a team
   granted to a group reaches the group's members. It also records them as a member of the org, so what a source shares
   with the whole company reaches them.
3. **Admission.** `org.yaml` says who gets in: `admission: teams` (a pilot) or `admission: org` (everyone the identity
   provider signs in, the default). During a pilot only members of an onboarded team, in any role, and org admins get
   in. A member means a role granted to the person or to one of their groups: a team granted to the whole org (a
   commons anyone may join) onboards nobody. Anyone else gets a 403 from the API and MCP ("your team isn't onboarded to Aerugo yet"), and the web app shows
   "Your team isn't on Aerugo yet" with a way to sign out, and nothing else. Service accounts always get in: an org
   admin registers each one.

Admission only narrows who gets in. It never changes what an admitted person may read: every document still carries
only its source's readers. It matters because some sources share with the whole company (a Confluence space anyone may
read, a public Slack channel, on a site or workspace confirmed as the company's): during a pilot only admitted people
reach those.

## Before the pilot

1. **Assign the app in the identity provider.** This is the outer door: people outside the pilot can't even sign in.
   - Entra: Enterprise applications, Aerugo's app, Properties: "Assignment required?" set to Yes. Under Users and
     groups, assign only the pilot's groups (assigning groups needs Entra ID P1 or P2).
   - Okta: only people and groups assigned to an app can sign in to it; assign the pilot's groups.
   - Google Workspace: turn the SAML app on only for the pilot's groups or organizational units.
2. **Put the groups in the token.** Aerugo reads Keycloak's own groups, by name. When Keycloak brokers the company's
   identity provider, map each pilot group to a Keycloak group, with the identity provider's mappers:
   - Entra (OIDC): in the app registration's Token configuration, add the groups claim (security groups, or groups
     assigned to the application). Entra sends each group's object id. In Keycloak, under the identity provider's
     Mappers, add an "Advanced Claim to Group" mapper per group: the claim `groups` with the group's object id, the
     Keycloak group it means, and sync mode Force.
   - Okta (OIDC): in the app's Sign On settings, a groups claim named `groups`, filtered to the groups Aerugo needs (for
     example, those starting with `aerugo-`). In Keycloak, an "Advanced Claim to Group" mapper per group, by name, sync
     mode Force.
   - Google Workspace (SAML): in the SAML app, map group membership to an attribute named `groups`. In Keycloak's SAML
     identity provider, an "Advanced Attribute to Group" mapper per group, sync mode Force.

   Sync mode Force re-applies the mapping at every sign-in through the identity provider, so someone taken out of a
   group loses the Keycloak group the next time they sign in. Name each Keycloak group as `team.yaml` names it.
3. **Set up the onboarding repository** from the example: `CODEOWNERS` gives every `team.yaml` and `org.yaml` to the
   platform team (they decide who's onboarded), and each team its own `sources.yaml`. CI plans on each pull request
   and applies on merge, as a service account.

## Start the pilot

One pull request:

- `org.yaml`: `admission: teams`.
- `teams/edw/team.yaml` and `teams/edp/team.yaml`, each with its admins and members by group, and their
  `sources.yaml`.

Its plan shows `aerugo_admission`, an `aerugo_team` per team, an `aerugo_team_member` per role holder, and an
`aerugo_connection` per source. Before approving, check that every member entry is one of the pilot's groups: a team
granted to a group everyone is in lets everyone in. Merging applies it.

Turning `teams` on in a deployment people already use refuses everyone outside an onboarded team within 30 seconds.
Tell them first.

## Add a wave

A pull request adding `teams/<team>/` (and the group's assignment in the identity provider, step 1). Once it's
applied, the team's members get in on their next request: a refusal is never remembered.

## A person joins or leaves

In the identity provider, by group. Joining takes effect at their next sign-in through it. Leaving takes effect at
their next sign-in too; until then their Keycloak session keeps the group (10 hours at most, by default), and an
admission already given holds for up to a minute. To cut someone off at once, end their sessions in Keycloak (Users,
the person, Sessions, Sign out) or disable them.

## End the pilot

`admission: org` in `org.yaml`, or remove it (destroying `aerugo_admission` sets `org`). Everyone signed in gets in
within 30 seconds. Lift the identity provider's assignment requirement, or assign everyone, in the same change.

## Check it

- `GET /v1/admin/admission` (org admins): the mode, who changed it and when, and the Terraform workspace managing it.
  `PUT /v1/admin/admission` with `{"mode": "teams"}` or `{"mode": "org"}` changes it outside Terraform; a change made
  that way is undone by the next apply when Terraform manages it.
- Each change is an `org.admission` audit event.
- For someone refused, `GET /v1/whoami` answers only `{"username": ..., "admitted": false}`.
- When OpenFGA or Postgres can't answer, requests get a 503 with `Retry-After`, never an admission.

## Limits

- Each API process trusts the mode it read for 30 seconds, and an admission for a minute.
- Proven in the local stack with Keycloak's own groups (smoke's `a_pilot_lets_in_only_onboarded_teams` and the
  browser test `test_during_a_pilot_someone_on_no_team_sees_why_and_nothing_else`). The identity providers' steps
  follow their documentation, not a real tenant.
