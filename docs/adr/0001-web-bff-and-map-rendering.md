# ADR 0001: The web app is a backend-for-frontend, and the knowledge map renders with sigma.js

**Status:** accepted (slice D2)

## Context

Slice D adds the first browser surface. Two choices carry long-term weight: where users' tokens live,
and what draws the knowledge map, which must stay fast with thousands of nodes and edges.

## Decision

**Next.js as a backend-for-frontend.** The web server signs users in with Keycloak (authorization code,
PKCE with S256, state bound to an httpOnly cookie, and a nonce). It keeps access and refresh tokens in
its own memory and gives the browser only an opaque, httpOnly, SameSite=Lax session cookie. Every API
call goes through its `/api/*` proxy, which adds the token on the server, refuses anonymous requests
with 401, and refuses cross-site POSTs. A script injected into a page can't read a token, because the
page never has one. The `kl-web` client is public (no secret to leak), allows only the code flow, and
has one redirect URI.

**sigma.js with graphology for the map.** sigma.js renders with WebGL and stays smooth into the tens
of thousands of nodes. graphology holds the graph and its ForceAtlas2 layout. Both are MIT licensed,
small, and run entirely in the browser, so no new server or service joins the cell.

## Alternatives considered

- **Tokens in the browser (a single-page app with PKCE in JavaScript).** Simpler, but any XSS can
  steal a token, and long-lived refresh tokens in the browser need extra defenses.
- **Cytoscape.js.** Mature, but canvas rendering slows down well before the sizes the map targets.
- **D3 force layouts with SVG.** Fine for hundreds of nodes; thousands become unusable.
- **Three.js-based graph libraries.** Capable, but heavier, and 3D adds little for an org map.

## Consequences

- Sessions are in memory, so restarting the web container signs everyone out. Production needs a
  shared session store (Redis or Postgres) once the web app runs as more than one replica (slice E).
- The web app is optional at runtime: it has its own Compose profile and `make web`, so the API, the
  worker, and `make smoke` never depend on its build.
- `GET /v1/graph` already enforces every permission rule, so the map can't show more than search would.
