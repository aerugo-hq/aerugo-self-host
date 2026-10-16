// The TypeScript client against a stand-in fetch: what it sends, and what it does with replies (node --test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { AerugoError, Client } from "../src/index.ts";

const seen: { url: string; init?: RequestInit }[] = [];
const standIn = async (url: string, init?: RequestInit): Promise<Response> => {
  seen.push({ url, init });
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  if ((init?.headers as Record<string, string>)?.Authorization !== "Bearer t0k3n") return json(401, { detail: "not signed in" });
  const u = new URL(url);
  if (u.pathname === "/v1/search") return json(200, { scope: ["payments"], results: [{ id: "kb-x" }], facets: { sources: [], containers: [] }, note: "" });
  if (u.pathname === "/v1/changes") {
    const since = Number(u.searchParams.get("since"));
    const feed = [{ cursor: 1, key: "a", change: "added" }, { cursor: 2, key: "a", change: "removed" }].filter((c) => c.cursor > since).slice(0, 1);
    return json(200, { changes: feed, next: feed.length ? feed[0].cursor : since, more: feed.length > 0 });
  }
  return json(404, { detail: "document not found" });
};

test("search sends the token and the filters", async () => {
  const kl = new Client("http://localhost:8000", "t0k3n", standIn);
  const r = await kl.search("lag", { source: ["jira", "confluence"] });
  assert.deepEqual(r.results.map((x) => x.id), ["kb-x"]);
  assert.equal(new URL(seen.at(-1)!.url).searchParams.get("source"), "jira,confluence");
});

test("a refusal is an error with its status", async () => {
  const kl = new Client("http://localhost:8000", () => "t0k3n", standIn);
  await assert.rejects(kl.document("kb-hidden"), (e: unknown) => e instanceof AerugoError && e.status === 404 && e.detail.includes("not found"));
});

test("the feed is followed to its end", async () => {
  const kl = new Client("http://localhost:8000", "t0k3n", standIn);
  const changes = [];
  for await (const c of kl.changes()) changes.push(c.change);
  assert.deepEqual(changes, ["added", "removed"]);
});

test("plain http is refused off localhost", () => {
  assert.throws(() => new Client("http://aerugo.example.com", "x", standIn));
});
