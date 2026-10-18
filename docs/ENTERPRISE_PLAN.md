# The enterprise plan: a self-evolving knowledge layer for every team

For Claude Code. This is the plan that sits above every other: it makes Aerugo the place a whole company's knowledge
lives, grows, and gets used, for every team, not one kind of project. Read, in order: `CLAUDE.md`,
`docs/BUILD_PLAN.md` (its rules, verification loop, and source recipe apply here too), `docs/PRODUCTION_REVIEW.md`
(its prerequisites come first), then this file. `docs/ORG_INTELLIGENCE_PLAN.md` becomes one scenario pack of this
plan (P8, engineering and data), and its Atlas one of its foundations.

## 1. Why: what people in companies actually struggle with

From published research and what employees say (Reddit and others), the same problems repeat in every company:

- **Time lost finding things.** McKinsey's estimate is about a fifth of the workweek spent looking for internal
  information or tracking down colleagues; Atlassian's 2025 survey of 12,000 knowledge workers found about a quarter.
- **Knowledge held by a few people.** Most knowledge is undocumented "tribal" knowledge in heads, chats, and hallway
  conversations; the symptoms are the departure cliff, the onboarding bottleneck, and the "ask a person" loop. When
  knowledge is mostly social, the leverage comes from mapping it.
- **Waiting and recreating.** People wait on colleagues for answers, or recreate what already exists somewhere.
- **Policies that change faster than people can follow.** In contact centers, agents search during live calls while
  the update they need is buried in daily emails; new agents take months to become proficient, and seasonal peaks
  multiply headcount at the worst moment.
- **Procedures needed at the worst moment.** Store teams work from strict procedures and escalation chains, and are
  told never to improvise policy; in an incident, the hard part is the exact procedure and the right person, now.
- **Cross-team crises.** A product recall needs quality, legal, PR, customer service, stores, e-commerce, and
  distribution acting in a precise order, some steps reviewed by a regulator, with a team that existed before the crisis.

So the core isn't search over documents, and isn't about one project. It's a layer that holds every kind of
knowledge (facts, procedures, policies, contacts, decisions, definitions, systems), captures what isn't written
down, keeps it current, and gets it to the right person, in the right form, at the moment they need it.

## 2. What it is, and what it isn't

**It is** a company's living memory: every source connected, every kind of knowledge held with its evidence, its
owner, its scope (team, role, location), and its freshness; growing from conversations, questions, and corrections;
answering anyone in their own context; guiding people through procedures and onboarding; and over time pointing out
what's missing, stale, contradictory, or held by one person.

**It isn't** an oracle or a monitor. It will be wrong sometimes, so every answer shows its evidence and how sure it
is, and every action is a person's decision. It never ranks or watches individual people: no one's questions,
answers, or activity are ever used to judge them (section 4, rule 7).

## 3. The scenarios it serves (a retailer as the example)

A fictional retailer of the kind of an outdoor-apparel brand, with stores, distribution centers, a contact center,
catalog and e-commerce, merchandising and planning, product development, marketing, finance, people teams, and IT
and data. The scenario packs (section 8) build these; the synthetic company (F9) tests them.

| Team | Moment | What it does |
|---|---|---|
| **Anyone new** | First week | A path for their role: the systems and people they'll work with, the procedures they'll use, exercises checked against the real knowledge, progress their lead can see |
| **Stores** | A customer slips and falls | Incident mode: the exact procedure for that kind of incident at that location, steps as a checklist, who to call by role (manager on duty, district, loss prevention, risk), the forms, what not to do |
| **Stores** | A return without a receipt | The current policy for their role and location, what changed and when, when to hand off to a store leader |
| **Contact center** | Live call | The current policy and its exceptions, the escalation rule, the approved script; this week's changes at the start of a shift |
| **Merchandising and planning** | "How is sell-through calculated in this report, and where does it come from?" | The metric's definition, the report, its data's lineage to the warehouse, its owner |
| **Allocation** | "Why did this store get fewer units?" | The allocation rules and their owners, the data behind it, past decisions |
| **Distribution** | A vendor's inbound shipment is late | The SOP, who to notify, what happened last time |
| **E-commerce** | Checkout errors spiking | Who's on call, what changed recently, the runbook, past incidents |
| **Marketing** | Holiday campaign | Brand guidelines, approved assets, who approves copy, last year's results |
| **Product and quality** | A defect is reported | The recall playbook: the team, the order of steps, regulatory steps, customer and store communication, e-commerce delisting, past recalls' lessons |
| **Finance** | Month-end close | The close checklist, owners, the systems and reports, last month's issues |
| **People** | "What's the leave policy for my location?" | The current policy for them, its owner (policy documents only; never anyone's personal records) |
| **IT, data, engineering** | Migrating the EDW to the cloud platform | The engineering and data pack (`docs/ORG_INTELLIGENCE_PLAN.md`): lineage, usage, conventions, plans, pull requests |
| **Security and compliance** | "Where does customer data flow?" | Lineage with evidence, the policies that apply, the owners |
| **Leaders** | Monthly | What changed, where knowledge is missing or stale, which knowledge only one person holds, where people get stuck |
| **Anyone leaving** | Handover | A guided handover interview that turns what they know into knowledge their successors can find |

## 4. Principles that never bend

Everything in `docs/BUILD_PLAN.md` section 2 and `docs/ORG_INTELLIGENCE_PLAN.md` section 3 applies. In addition:

1. **Every piece of knowledge has evidence, an owner, a scope, and a freshness.** Nothing is stated without where it
   came from; nothing is owned by no one for long (it's flagged); nothing lives forever without review.
2. **Perspectives are kept, not flattened.** When two teams say different things, both are kept with who said them
   and in what context; the conflict goes to their owners; the resolution is recorded, not overwritten silently.
3. **Capture is opt-in and visible.** A channel is captured only when its owners turn it on; its members see that it
   is; anyone can mark a message "don't capture"; direct messages are never captured by default.
4. **Sensitive topics stay out.** HR matters about individuals, medical and legal matters, and personal data are
   detected and excluded from capture and from answers, unless a governed space is configured for them.
5. **Answers fit the person.** Their team, role, and location (from the directory) decide which procedure, policy
   version, and contacts apply; a store associate and a buyer get different, correct answers to the same question.
6. **Role over person for contacts.** "Who do I call" resolves to a role and its current holder (an on-call rotation,
   a district's manager), so it stays right when people change.
7. **No monitoring of people.** No ranking of individuals by questions, answers, edits, or activity; aggregates only,
   never used for performance. Say so in the product.
8. **It evolves under test.** Knowledge, confirmations, playbooks, and thresholds evolve; every change to how it
   answers is held to its question sets (F7), never shipped untested.

## 5. The foundation: the knowledge core

New tables (each with row-level security from its evidence's readers, as the Atlas's are), and the pipelines around
them. The Atlas (`ORG_INTELLIGENCE_PLAN` O1) is the same idea for systems; both are part of the foundation.

- **Knowledge units** (`knowledge_units`): one piece of knowledge, typed:
  `fact`, `procedure` (ordered steps, each with its role, conditions, and forms), `policy` (versions with effective
  dates), `contact` (a role and how to reach it, its current holder resolved from the directory or a rotation),
  `decision` (K5's), `definition` (a term or metric, as the company uses it), `answer` (a canonical answer to a
  question asked often), `lesson` (what an incident or project taught). Each with: its text and structure, scope
  (teams, roles, locations), validity (effective from and until, superseded by), owner (a team or a role), trust tier
  (`confirmed` by its owner, `documented`, `observed` in conversation, `inferred` by a model), confidence, evidence
  (ids into documents, threads, tickets, the Atlas), readers (from that evidence), status (`proposed`, `confirmed`,
  `stale`, `superseded`, `retired`), and review cadence.
- **Perspectives** (`unit_perspectives`): who holds a different version of a unit (a team, a role, a person's
  attributed statement), its context, and its evidence; conflicts between them (`unit_conflicts`) with their
  resolution.
- **Questions** (`questions`): what people asked (text kept per the privacy rules), the answer's confidence, whether
  it was found, the feedback; the source of gaps (F5) and of question sets (F7). Aggregated; never shown per person.
- **Gaps** (`knowledge_gaps`): what's asked but not answered well, grouped, with how often and by which teams, the
  likely experts, and its state.
- **Paths** (`learning_paths`, `path_progress`): onboarding paths, their steps and exercises, and each learner's
  progress (visible to them and their lead).

The pipelines:

- **Extract**: from documents, threads, tickets, and transcripts, models propose knowledge units (a procedure from a
  runbook, a contact from "for X, ask Y's team", a definition from a glossary, an answer from a resolved thread), each
  held to `claims.py`'s checks against its evidence (after the production review's H4 fix), proposed until confirmed
  or until its evidence alone justifies `documented`.
- **Consolidate**: near-duplicate units merged with their evidence kept; newer and higher-trust units supersede
  older; contradictions detected and sent to owners as conflicts, never silently overwritten.
- **Answer**: answers composed from knowledge units first (the most specific to the person's scope, the highest
  trust, the freshest), then documents and the Atlas, with every claim cited, perspectives shown when they differ, and
  confidence stated; procedures returned as checklists, contacts resolved to the current holder.
- **Learn**: feedback and corrections become proposals to owners; confirmations raise trust; unanswered questions
  become gaps routed to likely experts; their answers (with their consent) become knowledge; stale units are sent for
  review on their cadence; question sets grow from real questions.

### Storage modes: data residency

The sources stay the system of record. What Aerugo keeps is chosen per source, by an admin, and shown in the app
(tab 3 of `docs/architecture/aerugo-architecture.drawio`; `docs/architecture/ARCHITECTURE.md`, section 3):

- **Index** (today): pointers and metadata, permissions, small derived facts, embeddings, and text passages.
- **Pointer**: everything but text. At answer time, the passages an answer needs are fetched live from the source,
  after the asker's permission is checked there, and discarded after the answer: never written to the database, a
  cache that outlives the request, a log, a trace, or a stored conversation (a stored answer keeps its citations and
  its own words, not the passages). The reader shows a document by fetching it live. Embeddings and derived facts are
  kept, with their sources' permissions, and say so in the connector's page: they are derived from content.
- **Live only**: pointers, metadata, and permissions; questions use each source's own search at answer time.

The Atlas's facts (who uses what, what feeds what) are always derived ahead of time; they're facts with pointers to
their evidence, never copies of documents. Everything kept stays in the company's own cloud project and region.

## 6. Capture: knowledge that isn't written down

- **Microsoft Teams**: an app with three parts. A message action ("Save to Aerugo") on any message, with the thread's
  context, proposing a unit for the poster to confirm. A bot that answers questions in Teams. And capture of standard
  channels whose owners opt in, through resource-specific consent (the bot then receives that channel's messages
  without being mentioned); resolved threads become proposed answers and procedures, sent to the channel's owner. Mind
  the platform's limits: group-chat delivery to bots is unreliable in practice, and live notifications for private and
  shared channels aren't available under resource-specific consent (they can be read on request), so the design rests
  on opted-in standard channels and the message action, never on watching chats.
- **Slack**: the same three parts, on the existing connector (message shortcut, bot, opted-in channels).
- **Meetings**: transcripts (Teams, Zoom) of meetings whose organizer opts in become proposed decisions, action items,
  and lessons, which attendees confirm.
- **Handover interviews**: a playbook for someone leaving or changing roles: a guided interview, built from what the
  graph says they own and what people ask them, turned into proposed units their successor and lead confirm.
- **Ask-time capture**: when an answer is missing, the asker can route the question to the likely expert (who to ask);
  the expert's reply, with their consent, becomes knowledge for the next person.
- **Shared mailboxes** (optional): announcement mailboxes (policy updates to stores and the contact center) whose owners
  opt in, as policy versions with effective dates.

## 7. Where people meet it

- **The web app**: everything below (section 9).
- **Teams and Slack**: ask, save, get this week's changes, run a procedure as a checklist, get onboarding steps.
- **Frontline devices**: a mobile-first view for store and distribution associates on handhelds and tablets: ask,
  incident mode, policy lookups, in few taps.
- **APIs and MCP**: for other tools and agents, within each caller's permissions.
- **Proactive**: when a policy or procedure changes, the roles it affects are told what changed, once, in their channel.

## 8. Scenario packs

A pack is the playbooks, templates, question sets, and connectors a family of scenarios needs, built on the
foundation. Each ships with its slice of the synthetic company (F9) and its question set, and is measured.

- **P1. Onboarding paths** (every team): a team lead designs a path (or accepts a drafted one: the systems, people,
  procedures, and definitions their role uses most); exercises are generated from the real knowledge and checked
  against it ("trace how an order reaches the warehouse", "find the escalation for a damaged return"); a first guided
  task; progress for the learner and their lead; paths kept current as their knowledge changes.
- **P2. Incident and emergency mode** (stores, distribution, IT): incident types per location with their procedure
  as a checklist, contacts by role and rotation, forms, escalation timing, and what not to do; opening the incident
  record in the company's tool (ServiceNow, PagerDuty) with approval; afterwards, lessons proposed from what happened.
- **P3. Policies and procedures** (stores, contact center, every team): the current version for the person's role and
  location, what changed and when, the owner, the hand-off rule; this week's changes as a digest.
- **P4. Contact center assist**: live-call answers in a side panel (policy, exceptions, escalation, approved script),
  later inside the contact center's own tool through its integration.
- **P5. Merchandising and planning**: metric and report definitions, where their data comes from (through the Atlas),
  owners, past decisions and hindsights.
- **P6. Supply chain and distribution**: SOPs, vendor contacts by role, exceptions and their handling, peak readiness.
- **P7. Crisis playbooks** (a recall, a data breach, an outage): the cross-team checklist in order, each step's owner
  role, regulatory steps, communication templates and who must approve them, lessons from the last one.
- **P8. Engineering and data**: `docs/ORG_INTELLIGENCE_PLAN.md`, all of it.
- **P9. Leaders' view**: knowledge health by team (gaps, stale units, unowned, single points of knowledge, conflicts),
  where people get stuck, adoption and time saved, in aggregate only.

## 9. Screens

Every screen in the app's existing shell and voice, permissions on everything, browser tests for each.

1. **Ask**, extended: answers from knowledge units with their type (a procedure shows as a checklist, a contact as a
   card with the current holder), perspectives when they differ, confidence, "this is wrong" and "route to an expert".
2. **Knowledge**: units by team, type, status, owner, and freshness; filters; bulk review.
3. **A unit's page**: its content and structure, versions, scope, evidence, perspectives, conflicts, owner, history.
4. **Procedure runner**: a procedure as a checklist to follow, step by step, with its contacts and forms (incident mode
   uses it).
5. **Policy page**: versions with effective dates, what changed, who it applies to.
6. **Who to call**: roles and their current holders, by team and location, with on-call rotations.
7. **Gaps**: what's asked and not answered, by team, with likely experts, and the routing.
8. **Review**: proposals to confirm, conflicts to resolve, stale units to review, for each owner (E2's queue, extended).
9. **Paths**: the builder for leads, the learner's view, progress.
10. **Capture settings**: channels opted in, their owners, exclusions, sensitive-topic rules, what was captured.
11. **Knowledge health**: P9's view.
12. **Frontline view**: mobile-first ask, incident mode, and policy lookup.
13. The org-intelligence screens (`ORG_INTELLIGENCE_PLAN` section 10) for P8.

## 10. The synthetic company, and measuring it

- **F9's synthetic retailer**: a generator that builds a fictional retailer with known truth: teams and roles,
  locations (stores, distribution centers, offices), a directory, documents of every kind (procedures, policies with
  versions, runbooks, glossaries, brand guidelines, close checklists), Teams and Slack channels with realistic threads
  (some answering questions that no document answers, some contradicting a document, some sensitive and to be
  excluded), meeting transcripts, tickets, incidents including a recall, and the right answer to each question, per
  role and location. Its size is a parameter. It's the only way to prove any of this works before a real company's
  data is involved.
- **Question sets per team** (`kl/eval/teams/*.yaml`): question, asker's role and location, the facts a right answer
  contains, the evidence it must cite, what it must not reveal to whom; graded automatically and some by people; held
  to baselines in CI, as `kl/evals.py` is.
- **Measures**: answers right per team and role; perspectives surfaced when they should be; sensitive content never
  captured or answered; gaps found against the truth; knowledge extracted from threads (precision and recall);
  staleness caught; onboarding exercises answerable; time to answer.

## 11. Build order

Phase 0 first, as `docs/ORG_INTELLIGENCE_PLAN.md` says (the production review's prerequisites). Then the pilot path
(G4), then the foundation, then capture, then the packs; each item on its own branch, proven on the synthetic company
and in the stack, recorded in `CLAUDE.md`, ticked here, stopping for review after each phase.

### The pilot path (decided 2026-10-17: right after Phase 0, before F9 and F10)
- [x] **G4.** Onboarding as code, with a pilot gate, so a company can start with two teams (say EDW and EDP) and add
      the rest a wave at a time: each team's folder in the onboarding repository declares the team, its admins, and
      its members by directory group (joining or leaving the group is the onboarding; no pull request per person);
      a pilot setting lets in only members of onboarded teams, beside the identity provider's own app assignment
      (Entra's "assignment required"), so nothing a source shares company-wide reaches people outside the pilot;
      each wave is a reviewed pull request whose plan shows what it connects.

### Foundation
- [ ] **F1.** Knowledge units, perspectives, and conflicts: the tables, row-level security from evidence, the API.
- [ ] **F2.** Extraction: units proposed from documents, tickets, and threads, held to the claim checks.
- [ ] **F3.** Consolidation: merging, superseding, contradiction detection, conflicts to owners.
- [ ] **F4.** Answers from units first: scope, trust, freshness; procedures as checklists; contacts resolved; perspectives shown.
- [ ] **F5.** The learning loop: feedback and corrections to owners; gaps from questions; routing to experts; answers kept with consent.
- [ ] **F6.** Lifecycle: review cadences, staleness, effective dates, retirement; owners told.
- [ ] **F7.** Question sets per team, a baseline, CI holding to it.
- [ ] **F8.** Context: the person's team, role, and location from the directory, used by every answer.
- [ ] **F9.** The synthetic retailer: the generator, its truth, its fakes for every capture source; `make company`.
- [ ] **F10.** The Atlas (`ORG_INTELLIGENCE_PLAN` O1 and O2): the systems graph and its synthetic enterprise.
- [ ] **F11.** Storage modes: index, pointer, and live only, per source; pointer mode fetching passages live with the
      asker's permission and keeping no text anywhere (smoke proves nothing of a pointer-mode source's text is in the
      database, a cache, a log, or a stored answer); the admin's choice shown per source; answer quality measured per mode.

### Capture
- [ ] **C1.** Teams: the message action, the bot, opted-in standard channels (resource-specific consent); sensitive topics excluded; exclusions honored.
- [ ] **C2.** Slack: the same three, on the existing connector.
- [ ] **C3.** Meeting transcripts, opt-in by organizer, confirmed by attendees.
- [ ] **C4.** Handover interviews.
- [ ] **C5.** Announcement mailboxes as policy versions (optional).

### Where people meet it
- [ ] **D0.** The UI revamp's design (decided 2026-10-17: after F1, so it reflects real knowledge units, and before
      D1): the information architecture for every entity (the organization and its sign-in, people, teams as spaces,
      sources and the chat channels mapped to them, knowledge, systems, reviews), the onboarding flows, and a
      clickable prototype reviewed before any D1 screen is built; then built alongside D1.
- [ ] **D1.** The Knowledge, unit, policy, procedure runner, who-to-call, gaps, and review screens.
- [ ] **D2.** Teams and Slack as places to ask, save, and follow procedures; change notices to affected roles.
- [ ] **D3.** The frontline view.

### Scenario packs
- [ ] **P1.** Onboarding paths.
- [ ] **P2.** Incident and emergency mode.
- [ ] **P3.** Policies and procedures, with change digests.
- [ ] **P4.** Contact center assist.
- [ ] **P5.** Merchandising and planning.
- [ ] **P6.** Supply chain and distribution.
- [ ] **P7.** Crisis playbooks, the recall first.
- [ ] **P8.** Engineering and data: `docs/ORG_INTELLIGENCE_PLAN.md`, O3 onwards.
- [ ] **P9.** The leaders' view.

## 12. When unsure

As in `docs/BUILD_PLAN.md`: stop and ask when a choice would widen who sees something, capture something people
didn't opt into, touch a sensitive topic, or write to a company's system; otherwise choose the narrower option and say
why. Nothing here is connected to a real company's systems, channels, or people without that company's approval, and
it's proven on the synthetic retailer first.
