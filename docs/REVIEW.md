# Review: the build from V to G3

A review of what was built after the handoff (`docs/BUILD_PLAN.md`), from the code and the repositories' history, on
2026-10-16. Fix in the order below. Each finding says what was found, why it matters, and what done looks like.

## Where things stand

- Every roadmap item is ticked on `g-governance`, the tip of a stacked chain of branches:
  `v-prove-whats-built` → `p3-smoke-github-opt-in` → `g1-openbao-least-privilege` (and `g1-on-main`) → `s2` … `s8` →
  `q-quality` → `e-experience` → `a-agents` → `g-governance`. `fix-cross-connection-documents` sits apart.
- **Nothing is merged: `main` holds only the initial import.**
- The provider repository is on a **detached HEAD** (`f86e052`), not a branch, with branches `s2` … `s7` and
  `g2-resources` beside it.
- The work lives in worktrees under `knowledge-layer/.claude/worktrees/awesome-sammet-725a52/` (`dev` on
  `g-governance`, `xconn` on `fix-cross-connection-documents`, `build` detached).

Verified as good: step V's fixes (`retire_exported` now matches imports' `source_kind = 'import'`); the provider's
`keepWhenUnset` (without it, pausing any connection replaced it); G1's policy (`deploy/openbao/services.hcl`: exactly
the services' paths, no listing, no `sys/` beyond the mount list); the webhook receiver (`kl/hooks.py`: a secret per
connection, the signature checked first and in constant time, Slack's replay window, one 401 for every refusal,
deliveries recorded once, a payload never trusted); `kl/attachments.py` (an attachment never has more readers than
its page). The records say plainly what wasn't built.

## P0: before anything else

1. **Prove the chain.** The only passing run on record is step V's (123 smoke checks). For S2 to G3, the checks are
   written, but no run of them is recorded. Run the whole loop (`docs/BUILD_PLAN.md`, section 4) at the chain's tip,
   fix what fails, and record each section's counts in `CLAUDE.md` as step V did. Until then, the ticks are claims.
2. **Put the provider back on a branch** (`git switch -c <name>` from the detached HEAD, or switch to the branch that
   holds its commit) before anything else touches that repository: work on a detached HEAD is lost when you switch away.
3. **Merge, in order, by pull request:** the chain into `main`, one section per pull request (V and P3, G1, S2 to S8,
   Q, E, A, G), each green in CI; then the provider's branches the same way; then push both repositories. Decide
   `fix-cross-connection-documents` (merge or drop). Then remove the worktrees.
4. **Review by section, not by commit:** Q1 to Q8 landed as one commit, as did E1 to E3, A1 to A4, and G2 and G3. Read
   each section's diff against the build plan's design before merging it.

## P1: correctness and security

5. **The claim checker passes contradictions** (`kl/claims.py`, Q3). With "not" among its stop words, and words under
   three characters dropped, it judges these claims supported by a passage that says the opposite:

   | Claim (the passage: refunds over $500 approved by the finance lead; records kept 90 days) | Checker says |
   |---|---|
   | Refunds over $500 are **not** approved by the finance lead | supported |
   | Records are kept for **30** days | supported |
   | Refunds over **$50** are approved by the finance lead | supported |
   | Refunds are **never** approved by the finance lead | supported |

   A check that marks a contradiction verified is worse than none. Keep negations (not, no, never, without, nor) and
   every number (with its unit or currency) as content words; a claim whose negation or numbers differ from its best
   passage's is unsupported. Put these four in its tests and in smoke, recalibrate, and only then let the team's model
   judge entailment on top, as planned.

6. **No connector survives a rate limit.** None of them (Jira, Confluence, Drive, Okta, and the new ones) handles
   429 or `Retry-After`; one throttled call fails the whole sync, and real sites throttle. Confluence makes a call per
   page for restrictions. Add one shared HTTP helper (429 and 503 honored, `Retry-After` respected, capped exponential
   backoff with jitter, a per-connection concurrency cap) and route every client through it. Give the fakes a throttle
   switch, and prove in smoke that a sync survives being throttled.

7. **Every sync lists everything.** Content is re-indexed only on a new version, but each sync lists whole containers
   and re-reads permissions item by item. At a real company's size that's slow and costly. Read changes since the last
   sync where the source offers it (Jira's `updated`, Confluence's last-modified ordering, Drive's changes feed and
   start page token, Graph's delta, Slack's cursors), and keep a full reconcile on a slower cadence for deletions and
   permissions.

8. **The webhook receiver reads the whole body before checking its size** (`kl/hooks.py`). Stream it with a cap, or
   rely on a limit at the edge, so an oversized request can't cost memory first.

9. **Graph and Drive notifications can be verified:** both echo a secret set when subscribing (Graph's `clientState`,
   Drive's channel token). Today they get no hook at all. Optional, but it would bring them down to minutes too.

## P2: measurement and reality

10. **The evaluation set is saturated:** 12 cases, Recall@10 100%, MRR 0.92. Q2's measure couldn't move (the roadmap
    says so), and Q4's and Q5's cases passed before they were built. Grow it to 40 to 60 cases that can fail: near
    duplicates, stale against current, distractors, answers that need two sources (through the fakes: Jira, Confluence,
    Slack, Drive), multi-hop lineage, and permission traps. Add answer-level checks (each claim supported, each
    citation right) once the claim checker is fixed. Then record a new baseline.

11. **Every source is proven only against fakes written from the vendors' documentation.** If a fake and the real API
    disagree, smoke passes and production fails. Before relying on any source, connect a real one (free tiers: an
    Atlassian site, a Slack workspace, a Notion workspace, a Microsoft 365 developer tenant, a Google Workspace trial),
    and record scrubbed real responses as contract fixtures the fakes are checked against.

## P3: known not built (from the records)

- S7: glossary terms as entries of their own.
- Q3: a team's model judging entailment on top of the word check (after finding 5).
- Q8: decisions in Ask's ranking beyond K5's; Drive's comments; attachments read by the media models; media in
  GitHub and Microsoft 365.
- G2: imports and secret stores as Terraform resources (decided against, with reasons: fine).
- Deployment, still to come: the Helm chart, the provider's private distribution, the runner.
