# Aerugo clients

Two small clients for Aerugo's API: search, Ask, documents, the change feed, and pipeline pushes. Each call acts as
whoever the token belongs to, so it sees exactly what they may see. The clients hold no key of their own.

- **Python** (`sdk/python`, package `aerugo`, needs `httpx`):

  ```python
  from aerugo import Client
  with Client("https://aerugo.example.com", token=access_token) as kl:
      hits = kl.search("replication lag", source=["confluence"])["results"]
      for change in kl.changes(since=cursor):
          print(change["cursor"], change["key"], change["change"])
  ```

- **TypeScript** (`sdk/typescript`, `@aerugo/client`, Node 22.18 or later, or a bundler):

  ```ts
  import { Client } from "@aerugo/client";
  const kl = new Client("https://aerugo.example.com", () => getAccessToken());
  const { results, facets } = await kl.search("replication lag", { source: ["confluence"] });
  ```

The token is a person's OIDC access token for the API, or, for `push`, a CI pipeline's workload token (a GitHub
Actions token for the `kl-ingest` audience). Pass a function instead of a string to hand each request a fresh one.
Plain `http` is refused except to a local stack. A refusal raises `AerugoError` with the API's status and message; a
document someone may not read is a 404, exactly like a missing one.

`make sdk-test` runs both clients' tests against stand-ins for the API.
