// Aerugo's TypeScript client (A2): search, Ask, documents, the change feed, and pipeline pushes. Every call acts as
// whoever the token belongs to (a person's OIDC access token, or a pipeline's workload token for pushes), so it sees
// exactly what they may see; the client adds nothing and holds no key of its own.

export interface SearchResult {
  id: string;
  title: string;
  source: string;
  source_kind: string | null;
  tier: "gold" | "silver" | "evidence";
  section: string | null;
  snippet: string;
  matched: string[];
  container: { ref: string; name: string | null } | null;
}
export interface Facets { sources: { kind: string; count: number }[]; containers: { ref: string; name: string | null; count: number }[] }
export interface SearchResponse { scope: string[]; results: SearchResult[]; facets: Facets; note: string }
export interface Change { cursor: number; key: string; change: "added" | "changed" | "removed"; source_kind: string | null; at: string }
export interface PushedDocument { key: string; title: string; uri: string; markdown: string; classification: string; readers: string[] }

export class AerugoError extends Error {
  readonly status: number;
  readonly detail: string;
  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

type Token = string | (() => string | Promise<string>);
type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export class Client {
  private readonly base: string;
  private readonly token: () => string | Promise<string>;
  private readonly fetcher: Fetch;

  constructor(baseUrl: string, token: Token, fetcher: Fetch = fetch) {
    if (!/^https:\/\/|^http:\/\/(localhost|127\.0\.0\.1)[:/]/.test(baseUrl)) {
      throw new Error("Aerugo is reached over https (http only for a local stack)");
    }
    this.base = baseUrl.replace(/\/+$/, "");
    this.token = typeof token === "function" ? token : () => token;
    this.fetcher = fetcher;
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const r = await this.fetcher(this.base + path, {
      method,
      headers: { Authorization: `Bearer ${await this.token()}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (r.status >= 400) {
      let detail = "";
      try { const j = await r.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail).slice(0, 200); }
      catch { detail = (await r.text()).slice(0, 200); }
      throw new AerugoError(r.status, detail);
    }
    return r.json() as Promise<T>;
  }

  search(q: string, opts: { scope?: string; limit?: number; source?: string[]; container?: string[] } = {}): Promise<SearchResponse> {
    const p = new URLSearchParams({ q, scope: opts.scope ?? "org", limit: String(opts.limit ?? 10) });
    if (opts.source?.length) p.set("source", opts.source.join(","));
    if (opts.container?.length) p.set("container", opts.container.join(","));
    return this.call("GET", `/v1/search?${p}`);
  }

  ask(question: string, model: string, opts: { scope?: string; sources?: string[]; containers?: string[] } = {}): Promise<Record<string, unknown>> {
    return this.call("POST", "/v1/ask", { question, model, scope: opts.scope ?? "org", sources: opts.sources, containers: opts.containers });
  }

  document(key: string): Promise<{ title: string; sections: { heading: string | null; body: string }[] }> {
    return this.call("GET", `/v1/documents/${encodeURIComponent(key)}/content`);
  }

  // Every change since a cursor, following the feed to its end; each carries its own cursor to resume from.
  async *changes(since = 0, limit = 100): AsyncGenerator<Change> {
    for (;;) {
      const page = await this.call<{ changes: Change[]; next: number; more: boolean }>("GET", `/v1/changes?since=${since}&limit=${limit}`);
      yield* page.changes;
      since = page.next;
      if (!page.more) return;
    }
  }

  // A pipeline's push, with its workload token as the client's token; poll the push id for its result.
  push(team: string, upserts: PushedDocument[] = [], deletes: string[] = []): Promise<{ push: string }> {
    return this.call("POST", `/v1/workspaces/${encodeURIComponent(team)}/documents`, { upserts, deletes });
  }
}
