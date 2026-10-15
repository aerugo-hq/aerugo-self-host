// Brand marks for the sources Aerugo connects, wherever a source shows (Documents, search, Ask's sources, the gallery,
// connections): each brand's own logo, so people can tell at a glance where something came from. Fetched once, as the
// web image builds, into public/brand/, so the app serves them itself and no page ever calls a third party. Each mark
// stays its owner's trademark, shown only to name their product.
//
// Full-color marks first, from Iconify's open "logos" set (its JSON package, else its API); one-color ones from Simple
// Icons where that has none. A mark already here in full color is kept; a one-color one is upgraded when a full-color
// one becomes available. One that can't be fetched is skipped, and the app shows a neutral glyph in its place. It never
// fails, so the image builds offline.
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";

const LOGOS_JSON = "https://cdn.jsdelivr.net/npm/@iconify-json/logos@1/icons.json"; // one major version
const LOGOS_API = "https://api.iconify.design/logos";
const SIMPLE = ["https://cdn.jsdelivr.net/npm/simple-icons@13/icons", "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons"];
const MARKS = { // our file name: its names in the logos set, then in Simple Icons
  github: { logos: ["github-icon"], simple: "github" },
  googledrive: { logos: ["google-drive"], simple: "googledrive" },
  confluence: { logos: ["confluence"], simple: "confluence" },
  jira: { logos: ["jira"], simple: "jira" },
  notion: { logos: ["notion-icon", "notion"], simple: "notion" },
  slack: { logos: ["slack-icon"], simple: "slack" },
  okta: { logos: ["okta-icon", "okta"], simple: "okta" },
  microsoftsharepoint: { logos: ["microsoft-sharepoint", "sharepoint"], simple: "microsoftsharepoint" },
  apacheairflow: { logos: ["airflow-icon", "airflow"], simple: "apacheairflow" },
};
// A mark is a plain drawing: no scripts, no embedded images or pages, no references outside itself (a gradient's #id is fine).
const UNSAFE = /<script|<foreignObject|<iframe|<image|\son[a-z]+\s*=|href\s*=\s*["']?\s*(?!#)|url\(\s*["']?\s*(?!#)/i;
const OUT = new URL("../public/brand/", import.meta.url);
const SOURCES = new URL("sources.json", OUT); // where each mark came from

const get = async (url, as = "text") => {
  const r = await fetch(url, { signal: AbortSignal.timeout(20000) });
  if (!r.ok) throw new Error(`${r.status}`);
  return as === "json" ? r.json() : r.text();
};
const safe = (svg) => typeof svg === "string" && svg.trimStart().startsWith("<svg") && svg.length < 200000 && !UNSAFE.test(svg);
const has = async (url) => stat(url).then(() => true, () => false);

await mkdir(OUT, { recursive: true });
const from = await readFile(SOURCES, "utf8").then(JSON.parse, () => ({}));
const set = await get(LOGOS_JSON, "json").catch(() => null);
const fromSet = (name) => {
  const icon = set && (set.icons[name] || (set.aliases && set.aliases[name] && set.icons[set.aliases[name].parent]));
  if (!icon) return null;
  const w = icon.width ?? set.width ?? 16;
  const h = icon.height ?? set.height ?? 16;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}">${icon.body}</svg>`;
};
const done = { color: [], mono: [], kept: [], missing: [] };
for (const [file, names] of Object.entries(MARKS)) {
  const path = new URL(`${file}.svg`, OUT);
  if (from[file] === "logos" && await has(path)) { done.kept.push(file); continue; }
  let svg = null;
  for (const name of names.logos) {
    svg = fromSet(name) || await get(`${LOGOS_API}/${name}.svg`).catch(() => null);
    if (safe(svg)) break;
    svg = null;
  }
  if (svg) {
    await writeFile(path, svg);
    from[file] = "logos";
    done.color.push(file);
    continue;
  }
  if (await has(path)) { done.kept.push(file); continue; } // a one-color mark from before: kept
  for (const base of SIMPLE) {
    svg = await get(`${base}/${names.simple}.svg`).catch(() => null);
    if (safe(svg)) break;
    svg = null;
  }
  if (svg) {
    await writeFile(path, svg);
    from[file] = "simple-icons";
    done.mono.push(file);
  } else {
    done.missing.push(file);
  }
}
await writeFile(SOURCES, JSON.stringify(from, null, 2));
console.log(`Brand marks: ${done.color.length} in full color, ${done.mono.length} in one color, ${done.kept.length} already here` +
            (done.missing.length ? `; not available, so a neutral glyph shows instead: ${done.missing.join(", ")}` : "") + ".");
