// Where a page lives (slice N1), as in Harness's scopes: #/t/<team>/<page> is a team's, anything else the org's.
// Links inside a scoped page stay in its scope, so another team's knowledge appears only when you go up to the org.
export const SCOPED = new Set(["home", "documents", "ask", "search", "map", "proposals"]);

export function route(hash = typeof window === "undefined" ? "" : window.location.hash) {
  const parts = hash.replace(/^#\/?/, "").split("/");
  let team = "";
  if (parts[0] === "t" && parts.length > 1 && parts[1]) {
    team = decodeURIComponent(parts[1]);
    parts.splice(0, 2);
  }
  const [view, ...rest] = parts;
  return { team, view: view || "home", arg: decodeURIComponent(rest.join("/")) };
}

// The address of a page: a team's when team is set, the org's otherwise.
export const at = (team, view, arg = "") => `#/${team ? `t/${encodeURIComponent(team)}/` : ""}${view}${arg ? `/${arg}` : ""}`;

// An org-level link ("#/ask/...") kept inside a team's scope; unchanged at the org.
export const inScope = (path, team) =>
  team && team !== "org" && path.startsWith("#/") ? `#/t/${encodeURIComponent(team)}/${path.slice(2)}` : path;
