# Storage modes: what Aerugo keeps from a source

Each Confluence, Jira, and Slack connection chooses how Aerugo keeps what it reads. Every other source is indexed.

| | Index (the default) | Pointers only | Live only (Confluence and Jira) |
|---|---|---|---|
| Kept | Each item's address, title, version, readers, its text in sections, and embeddings | Each item's address, title, version, readers, and embeddings. No text, no section headings; a Slack thread is titled by its channel and day, not its first message | Each item's address, title, version, and readers. No text and no embeddings |
| Search | Every channel: words, meaning, titles, and other names | Titles and meaning only: no keyword search over the text, and no snippet in the results | Titles, and the source's own search, kept to what the asker may read; no snippet |
| Ask and the reader | Answer from the kept text | Read the item from its source when someone asks, for that person only, then drop it | As pointers |
| Made from the text | Bronze pages, entities, references, personal-data scans, extraction's proposals, and compiled drafts | None of them. Units people wrote and confirmed stay theirs | None of them, as pointers |
| Attachments | Their own documents | Left out: they aren't read live | Left out |
| When the source is down | Answers still work | Its items can't be read: they drop out of answers, and the reader says the source didn't answer | Its items can't be found or read |

## How a pointer is read

When an answer or the reader needs a pointer's text, Aerugo asks the source for the item and asks it again who may
read it now, the same way a sync works that out: Jira's own permission search, or the project's readers; Confluence's
space grants narrowed by every restriction from the page up; a Slack channel's current members. It goes ahead only
when the person asking is among them, through the accounts linked to them. Anything else reads as nothing: a source
that doesn't answer, an item it no longer has, a page moved out of its space or an issue moved out of its project, a
restriction it won't show, a person it no longer lists. The index can lag a source by a few minutes (the readers
refresh); a pointer's live read can't.

The text goes to the model the team chose for the answer, under the same egress rules as indexed text, and is dropped
once the answer is made. A stored answer keeps only its own words and its citations (titles and addresses). Nothing
read live is logged.

## How a live-only source is searched

Aerugo keeps no text and no embeddings for a live-only connection, so a search (in smart mode) asks the source: Jira's search (JQL) or
Confluence's (CQL), for the question's longest content words, within only the projects or spaces where the person
already opens something, as the connection's account. Only letters and digits ever reach the query, so nothing a
person types becomes JQL or CQL. The account usually sees more than the person asking, so only the items' keys are
used: each must be a document the person may open (Aerugo's own readers, then the authorization check) before anything
is ordered, so nothing the source found that the person can't open is shown, counted, or moves what they can see; an
answer then reads each live as a pointer, checking the source's readers again. Within those projects and spaces, an
item the person can't open (under a security level or a page restriction) can still take one of the source's 50 result
places. Every live-only source is asked at once and given a few seconds; a slow or throttled one is left out of that
search. Slack can't be live only: its search reads as a person, never as a bot, so a Slack connection keeps pointers
instead.

## Choosing it

- **In the app:** Admin, then Sources, then the connection's form ("Keep"), or Edit on a connection's row. A row says
  "Pointers only" or "Live only" when that's how it keeps what it reads.
- **Through the API:** `"storage": "pointer"` when creating a Confluence, Jira, or Slack connection, or `"live"` for
  Confluence and Jira, or in a `PATCH` of one. Any other source refuses them.
- **Changing it** reads everything again in the new form. Leaving index deletes the kept text and everything made from
  it as each item is read again; going back to index keeps it again.

## Trade-offs

- Answers are slower and depend on the source being up: each pointer is a few calls to its source when it's used, and
  each search asks every live-only source in scope. While someone waits, each call is tried once and briefly, never
  waiting out a throttle, so a busy source drops out of that answer and the syncs keep the account's rate.
- Search is weaker: with pointers, a question has to match an item's title or meaning, not a word buried in its text;
  live only depends on the source's own search.
- Embeddings still reveal what a text is about. With pointers they're kept under the same readers as the item, and
  never shown; live only keeps none.
- Not built yet: pointers and live only for other sources, Terraform's `storage` attribute (the provider's next
  release), and attachments read live.
