# Storage modes: what Aerugo keeps from a source

Each Confluence, Jira, and Slack connection chooses how Aerugo keeps what it reads. Every other source is indexed.

| | Index (the default) | Pointers only |
|---|---|---|
| Kept | Each item's address, title, version, readers, its text in sections, and embeddings | Each item's address, title, version, readers, and embeddings. No text, no section headings; a Slack thread is titled by its channel and day, not its first message |
| Search | Every channel: words, meaning, titles, and other names | Titles and meaning only: no keyword search over the text, and no snippet in the results |
| Ask and the reader | Answer from the kept text | Read the item from its source when someone asks, for that person only, then drop it |
| Made from the text | Bronze pages, entities, references, personal-data scans, extraction's proposals, and compiled drafts | None of them. Units people wrote and confirmed stay theirs |
| Attachments | Their own documents | Left out: they aren't read live |
| When the source is down | Answers still work | Its items can't be read: they drop out of answers, and the reader says the source didn't answer |

## How a pointer is read

When an answer or the reader needs a pointer's text, Aerugo asks the source for the item and asks it again who may
read it now, the same way a sync works that out: Jira's own permission search, or the project's readers; Confluence's
space grants narrowed by every restriction from the page up; a Slack channel's current members. It goes ahead only
when the person asking is among them, through the accounts linked to them. Anything else reads as nothing: a source
that doesn't answer, an item it no longer has, a restriction it won't show, a person it no longer lists. The index can
lag a source by a few minutes (the readers refresh); a pointer's live read can't.

The text goes to the model the team chose for the answer, under the same egress rules as indexed text, and is dropped
once the answer is made. A stored answer keeps only its own words and its citations (titles and addresses). Nothing
read live is logged.

## Choosing it

- **In the app:** Admin, then Sources, then the connection's form ("Keep"), or Edit on a connection's row. A row says
  "Pointers only" when that's how it keeps what it reads.
- **Through the API:** `"storage": "pointer"` when creating a Confluence, Jira, or Slack connection, or in a `PATCH` of
  one. Any other source refuses it.
- **Changing it** reads everything again in the new form. Going to pointers deletes the kept text and everything made
  from it as each item is read again; going back to index keeps it again.

## Trade-offs

- Answers are slower and depend on the source being up: each pointer is a few calls to its source when it's used.
- Search is weaker: a question has to match an item's title or meaning, not a word buried in its text.
- Embeddings still reveal what a text is about. They're kept under the same readers as the item, and never shown.
- Not built yet: pointers for other sources, Terraform's `storage` attribute (the provider's next release), and
  attachments read live.
