# Information architecture (D0)

Where every entity in Aerugo lives, how each person finds it, and how an organization, a team, a person, and a source
get started. Designed 2026-10-23 against the knowledge units F1 to F8 built, with a clickable prototype in
`docs/design/prototype/` (open `index.html` in a browser; it links the app's own stylesheet, so its tokens and
components are the app's). D1 builds the screens from it, in the app, once the prototype is reviewed; the prototype is
then deleted. `DESIGN.md` still governs how anything looks; this file governs where it is.

## The five decisions (approved 2026-10-23)

1. **One Review inbox.** Everything waiting on someone is in one place, whatever made it: a model's proposal, a saved
   message, a meeting's or a handover's proposal, an expert's kept answer, a conflict, a stale unit, a correction, a gap,
   a curated draft. It replaces Proposals, Knowledge health's queue, and the separate handover and meeting reviews.
2. **Home fitted to role and location.** What changed for you this week, in your role and place; the checklists you're
   following; what's waiting for you; and an ask box. Not a list of teams.
3. **Knowledge is one place, kinds are filters.** Policies, procedures, contacts, definitions, decisions, lessons,
   facts, and kept answers are one list with filters. Each kind still opens in its own shape: a policy with its versions
   and effective dates, a procedure as a runner, a contact as a card with its current holder, a definition with its
   perspectives.
4. **The prototype is static HTML** in `docs/design/prototype/`, no dependencies, never shipped.
5. **The prototype covers** D1's screens, the onboarding flows, and the frontline view. The engineering screens (the
   Atlas, Usage, Migrations) appear only as entry points; they're designed at O3.

## Entities, and where each lives

| Entity | What it is | Address | Who sees it | Its page shows |
|---|---|---|---|---|
| The organization | One company: its sign-in, admission (a pilot or everyone), and org admins | `#/settings/organization` | Org admins | Admission, the directory, models across teams, compliance |
| You | The person signed in: name, title, place, manager, teams and roles, linked accounts | `#/you` | Yourself | Your context as answers use it (F8), linked accounts, your questions |
| A colleague | Someone named by the directory or a source | in Who to call, and a unit's owner | Anyone who reads what names them | Name and title only; never their record |
| A team | A space: its people, sources, knowledge, and reviews | `#/t/<team>/home` | Its members; findable teams by name | Overview, Knowledge, Documents, Review (curators), Settings (admins) |
| A source | A connection a team made: Confluence, Jira, Slack, Teams, Drive, SharePoint, Notion, catalogs | `#/settings/sources/<id>` | The team's admins (org admins for directories) | Its containers, freshness, storage mode, and, for chat, each channel's capture and answers |
| A container | What a source holds: a space, project, channel, drive, site, or captured meeting | inside its source | As its source | For a chat channel: capture off, requested, or on, and who turned it on |
| A document | Evidence from a source, with the source's own readers | `#/documents/<id>` | Its source's readers | The reader, where it sits in its source, Open in |
| A knowledge unit | One piece of knowledge with its evidence, owner, scope, trust, and freshness | `#/knowledge/<id>` | Whoever reads all of its evidence (F1) | Its kind's shape, scope, evidence, perspectives, conflicts, versions, history |
| A review item | Something waiting on a person | `#/review/<tab>` | Exactly who must act: owner team's curators, a meeting's people, a handover's successor or lead | What it is, why it's yours, its evidence, its actions |
| A gap | A question several people asked and weren't answered | `#/review/gaps` | The askers' teams' curators | How often and by how many, never by whom; closest pages; likely experts |
| A handover | Someone leaving or changing roles, their prompts and shared answers | `#/handovers[/<id>]` | Its person; counts for its successor, lead, and requester | As built in C4b |
| A system | A node in the Atlas: a table, job, repo, dashboard (P8) | `#/systems/<id>` | Whoever may see its evidence | Designed at O3 |

Two words people meet that the system doesn't use: a unit's kind is said in plain words (a policy, a procedure, who to
call), never "unit" on default views; and "proposal" means anything waiting to be confirmed, whatever proposed it.

## Navigation

The shell stays: a rail that opens on hover or focus, a thin top bar, one view at a time, the scope picker (the whole
org, or one team), and ⌘K.

| Group | Item | Who sees it |
|---|---|---|
| | Home | Everyone |
| Know | Ask | Everyone |
| | Knowledge | Everyone |
| | Who to call | Everyone |
| | Documents | Everyone |
| | Map | Everyone (the Atlas joins it as Systems for data teams, P8) |
| Act | Review, with a count | Anyone with something waiting: curators, a meeting's people, a handover's successor or lead |
| | Handovers | Everyone |
| Admin | Settings: Sources, People, Models, Capture, Activity | Team admins (their teams) |
| | Organization, Compliance, Alerts | Org admins |

Who to call is its own item: it's the one thing frontline people reach for under pressure, so it's one tap from
anywhere. Review appears only when something is waiting, with its count; the count is the person's own.

### Addresses, old and new

| Today | After D1 |
|---|---|
| `#/proposals[/<id>]` | `#/review/drafts[/<id>]` (curated drafts, one tab of the inbox) |
| `#/t/<team>/health` (its queue) | `#/review/stale` and `#/review/conflicts`; the health summary moves to the team's Overview |
| gaps (API only) | `#/review/gaps` |
| `GET /v1/handovers/review`, `/v1/meetings/review` | `#/review/confirm`, each item saying what proposed it |
| `#/admin/<section>` | `#/settings/<section>` (sources, people, models, capture, activity) |
| none | `#/knowledge[?kind=]`, `#/knowledge/<id>`, `#/run/<id>`, `#/people`, `#/you` |

Old addresses redirect to their new place for a release.

## Home, fitted to role and location

| Block | What it shows | Who | Built on |
|---|---|---|---|
| Ask | One box, with two or three questions people in your role asked this month | Everyone | Ask; F5a's questions, aggregated (never one person's) |
| This week for you | Units that changed and apply to your role and place: what changed (before and after for a new version), who owns it, why it applies to you | Everyone | Unit events (F6) and scope matching (F8); a new endpoint in D1 |
| Your checklists | Procedures you started and haven't finished, and your onboarding path | Everyone who started one | The runner's progress (D1 keeps it per person) |
| Waiting for you | The Review inbox's count by tab, oldest first | Anyone with something waiting | The inbox |
| Who to call here | The three roles people at your place call most, with their current holders | Frontline roles | Contact units and holder resolution (F8) |
| Your team's knowledge | Gaps, stale, unowned, conflicts, in counts | Team admins and leads | Knowledge health (E2), P9 later |
| Getting started | The setup checklist (see onboarding) | Org and team admins until done | Each step's own state |

Nothing on Home names a person's questions, drafts, or anyone's record.

## Knowledge

One list: kind, title, owner team, trust, freshness, and who it applies to. Filters: kind (all, policies, procedures,
who to call, definitions, decisions, lessons, facts, answers), team, status (current, due for review, proposed),
and "applies to me" (on by default: the person's role and place, F8). Server-side paging and filtering; counts per kind
only over what the person may read (invariant 11).

Each kind's page:

| Kind | Page | Shows |
|---|---|---|
| Policy | `#/knowledge/<id>` | The current rule large, effective from; earlier versions with their dates and what changed; who it applies to; owner; evidence |
| Procedure | `#/knowledge/<id>`, and `#/run/<id>` to follow it | Its steps with role, condition, and form; contacts it names with their holders; Start, which opens the runner |
| Contact | in Who to call, and `#/knowledge/<id>` | The role, its current holder for you (never a guess: none when two or none hold it), how to reach them |
| Definition | `#/knowledge/<id>` | The term, its definition, and other teams' or roles' perspectives side by side, each said as its holder's |
| Decision | `#/knowledge/<id>` | What was decided, by whom, when, what it replaced |
| Lesson, fact, answer | `#/knowledge/<id>` | Its text, scope, evidence; for an answer, where it came from (a saved message, a handover, an expert) |

Every unit page has the same frame: kind and status in words, owner team and role, trust ("confirmed by Store
operations on 3 Oct"), scope, evidence (only documents you open), perspectives, conflicts, history. Actions: "This is
wrong" (a correction to its owners, F5a) for everyone; Confirm, Retire, New version for whoever may settle it.

### The procedure runner (the one bold screen)

A procedure followed step by step, built for someone under pressure. The current step is set large (18/28), with its
role, condition, and form; the steps done above it, collapsed; the steps to come below, quiet. The contacts it names sit
beside it with their holders and a call link; a California store sees California's extra step (scope, F8). Progress is
the person's own. Incident mode (P2) and onboarding paths (P1) are this runner with more around it. On a phone, the
step fills the screen and the contacts become a bar at the bottom.

## Review: one inbox

| Tab | Items | Who sees an item | Actions |
|---|---|---|---|
| To confirm | Proposals: from documents (a model, F2), a saved message (C2c, C1b), a meeting (C3), a handover (C4), an expert's kept answer (F5b), a curator's draft | The owner team's curators; a meeting's people for its proposals; a handover's successor or lead for its | Confirm, Not right (asks first), Edit and confirm |
| Conflicts | Two units that disagree (F3) | Curators of either side, only when they read both | Keep one, keep both with a note |
| Due for review | Stale units: review due, or evidence changed (F6) | The owner team's curators | Still right (confirm), New version, Retire |
| Corrections | "This is wrong" notes (F5a), never naming the sender | The owner team's curators | Fix it (new version), Still right |
| Gaps | Questions several people weren't answered on (F5a) | The askers' teams' curators | Write the answer, Ask an expert, Dismiss |
| Drafts | Curated pages as pull requests (proposals today) | The team's curators | As today |

Each item says why it's yours in one line ("You're Payments' curator", "You were in this meeting", "You're carol's
successor"). The inbox is per person and counts only what they may act on.

## Ask, extended

Answers lead with knowledge units in their kind's shape: a procedure as a short checklist with Start, a contact as a
card with its holder, a policy with its effective date, a definition with any other team's view said as theirs.
Confidence in words with its reasons. Under each answer: "This is wrong" and "Ask an expert" (F5b's routing, offered when
the answer is unsure or the person asks).

## Onboarding flows

| Flow | Who | Steps | Done when |
|---|---|---|---|
| The organization | The first org admin | Sign in (SSO) → choose who gets in (a pilot of named teams, or everyone) → bring in teams (from the onboarding repository, G4, or here) → connect the directory (people, managers, places) → register a model → connect each pilot team's first source → see the first answers | Each pilot team has a source synced and a model |
| A team | A team's admin | Their team's Home shows "Get your team ready": connect a source → choose which channels to capture (each also needs its owner's key) → choose the team's model → add the team's question set (F7) → tell the team | The team's question set passes its baseline |
| A person's first sign-in | Anyone | Welcome: who Aerugo thinks you are (title, place, manager, from the directory; "something wrong? tell your admin") → your teams → link accounts whose documents you should see (GitHub, Google) → ask your first question | Dismissed, or a first question asked |
| A source | A team's admin | Choose a source → what it needs (read-only account, scopes, plainly) → its credential (stored, or the team's own secret reference) → what to read (spaces, projects, channels) → how to keep it (indexed, pointers, live) → first sync, with what came in and who can read it | The first sync finished |

During a pilot, someone whose team isn't onboarded sees only the page saying so (G4, as built).

## The frontline view

A phone-first view for store and distribution associates (D3), the same app at 375 pixels with three things one tap
away: Ask, Incident (the runner for the procedure that fits where they are), and Look up a policy. Who to call is
always in the bottom bar. Large targets (44 pixels), the reading size for answers, nothing else on screen.

## What D1 builds, and the APIs behind each screen

| Screen | Exists | New in D1 |
|---|---|---|
| Knowledge list | `GET /v1/units` | "applies to me" filter; counts per kind |
| A unit's page | `GET /v1/units/{id}` (versions, perspectives, conflicts, history) | Kind shapes in the web app |
| Procedure runner | the unit's steps | Progress per person (a small table, the person's own rows) |
| Who to call | contact units, `context.holder` | `GET /v1/contacts` by place and team |
| Review inbox | `/workspaces/{ws}/review`, `/conflicts`, `/gaps`, `/handovers/review`, `/meetings/review`, proposals | One `GET /v1/review` gathering them, per person |
| Home | whoami's context, unit events | `GET /v1/changes/for-me` (unit events matched to role and place) |
| Settings, Sources | connections and their containers | The channel list with capture and answers in one place |
| Onboarding | admission, connections, models, question sets | A setup state per org and team |

Not in D0 or D1: the engineering screens (O3 on), paths (P1), the leaders' view (P9).
