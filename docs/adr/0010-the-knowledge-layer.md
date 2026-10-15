# ADR 0010: The knowledge layer is the product

Status: Accepted (2026-10-13)

## Context

Aerugo started as a permission-aware search over curated OKF bundles and synced evidence. Working through what
enterprises actually need (a data team migrating a legacy warehouse to a cloud platform, legal and marketing sharing
knowledge without seeing each other's private work, agents that need the company's context) showed that the value is
not the search box. It is a layer that knows what exists, how it connects across teams and systems, who owns it, and
what was decided, and that every other tool (people, agents, RAG systems, migrations) can build on.

The companies that do this best internally (Google's Moma, Glean, Microsoft's semantic index over Microsoft Graph,
Spotify's Backstage catalog, LinkedIn's DataHub) share one pattern: their links come mostly from signals the company
already produces (explicit references, ownership, systems and lineage, activity), with AI filling the gaps, and
permissions checked on every hit. Research on graph retrieval adds two warnings: expanding through a graph leaks unless
every hop is authorized, and precomputed summaries across documents with different readers leak by construction.

## Decision

1. **The knowledge layer is the product.** Its components are Extract, Evidence, Graph, Curated, Quality, Policy, and
   Interfaces. Connectors, the web app, and the hosted service are built around it, never into it.
2. **Documents stay in their sources.** Aerugo keeps what it extracts (text, chunks, embeddings, metadata, the graph,
   permission mirrors) in its own store: inside the company's environment when self-hosted, in the company's region on
   the hosted service. Large media is a pointer plus derived text. Optionally, an original's copy is kept, encrypted
   with its team's key.
3. **Two layers of knowledge.** Evidence is everything; curated pages are the small, reviewed layer of truth. Curated
   pages are OKF, stored in Git or in Aerugo's own versioned store (with review inside Aerugo, for teams that don't use
   Git). OKF is also the portable export. Nobody has to turn their documents into OKF.
4. **The graph is the moat, and it is built from signals first.** Recorded links (references, attachments, lineage,
   owners) are harvested from every connector; inferred links (entity resolution, similar meaning) fill gaps with a
   confidence and a reason; confirmed links come from people and become rules. Systems, tables, jobs, and dashboards
   are first-class nodes, so migrations and impact analysis work. Every link carries its provenance.
5. **Precision over completeness.** Aerugo would rather miss a link than show a wrong one. Unsure links and merges go
   to a review queue.
6. **Permissions shape every derived view.** Graph walks are authorized at every hop; summaries, cluster labels, and
   entity descriptions are built only from what the viewer can read.
7. **Quality is measured, not claimed.** Evaluation sets with known answers and permission traps run on every change;
   accuracy is shown per company on a knowledge-health page. Confidence comes from signals and calibration, never from
   a model's opinion of itself.

## Consequences

- The Map becomes a view of the graph: clusters, bridges, and link kinds drawn differently (recorded solid, inferred
  dashed, confirmed bold).
- New invariants 14 to 16 in `CLAUDE.md`: provenance on every link, per-hop authorization, and no derived view that
  mixes permissions.
- The build order is the K slices in `CLAUDE.md`: the evaluation set first, then recorded links, systems and data,
  understanding, decisions, people, the Map, the walk, confidence, review and health, extraction gaps, and interfaces.
- Perfection is not the promise. Measured accuracy, shown openly, with honest uncertainty, is.
