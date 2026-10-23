# Handovers

When someone leaves or changes roles, a handover turns what they know into knowledge their successor can find. It's a
guided interview in the web app: prompts built from what they own, answered in their own words, and shared only when
and with whom they choose. The decisions behind it are in `docs/ENTERPRISE_PLAN.md` (C4).

## Who starts one

- **The person**, for themselves: it opens at once. They name their team (one they're on), why (leaving or changing
  roles), their successor, and their lead (their manager, as the company directory says, unless they name someone).
- **Their manager or one of the team's admins**, as a request: it waits for the person, who accepts (it opens) or
  declines (it ends, and nothing of theirs is kept). A request's lead is whoever asked, unless they name someone.

Successors and leads are named by username and must have signed in to Aerugo. Nobody else can start one for someone.

## What it asks

Only about work the person owns, built as them, so a prompt never names something they can't read:

- the curated pages they own or vouched for;
- the knowledge units they wrote or confirmed;
- the documents they wrote (Git authorship, for people who linked their GitHub account);
- the questions people routed to them (ask an expert);

at most 20 of those, then five open prompts: what they do regularly that isn't written down, who to call and for what,
what breaks and how to fix it, what's in flight, and where things live that people can't find. A handover never asks
about colleagues or anyone's record.

## Drafts and sharing

Drafts are the person's alone: the database itself shows them to no one else, not the successor, the lead, the
requester, or an admin. Those people see only a handover's status and how many answers are shared.

Each answer is shared on its own, with the audience the person chooses:

- **the team, the successor, and the lead** (the default): the handover's team as it holds its members, plus the two
  people named, never a grant to the whole company;
- **everyone in the company**.

A shared answer becomes a document of kind `handover`, headed by its kind of prompt ("Who to call", "What breaks, and
how it's fixed") and never by what the prompt was about, since that could name a page the audience can't read. It's
also proposed as a knowledge unit (a fact; a lesson for what breaks; an answer for what people ask), trusted as what
someone said, so no answer uses it until it's confirmed. An answer that speaks of one person's health, discipline,
performance, or pay, or holds personal data, can't be shared.

## Who confirms

The successor or the lead named on the handover, or a curator of its team, confirms or retires the proposed unit. The
database enforces it: only someone the handover names, who can read the shared answer, can settle a unit built from
handovers alone. Its history says who confirmed it ("its handover's successor or lead").

## Withdrawing, and leaving

Until they leave, the person can withdraw any shared answer: its document and its unit go, confirmed or not, and it's a
draft again. When a connected directory says they've left, every unshared draft is deleted and their handover closes;
what they shared stays with its audience, which is the point of a handover.

## The API

| Call | Who | What |
|---|---|---|
| `POST /v1/handovers` | the person, their manager, or a team admin | start one, or request one of someone |
| `GET /v1/handovers` | anyone | yours, the ones you requested, and the ones naming you, with counts only |
| `POST /v1/handovers/{id}/accept`, `/decline` | the person | answer a request |
| `GET /v1/handovers/{id}/answers` | the person | the prompts and your drafts |
| `PUT /v1/handovers/{id}/answers/{answer}` | the person | save a draft and its audience |
| `POST /v1/handovers/{id}/answers/{answer}/share`, `/withdraw` | the person | share or withdraw one answer |
| `GET /v1/handovers/review` | a successor or lead | the proposals waiting for you |

Anyone else's handover is a 404, as a missing one is. Nothing is sent outside the app.
