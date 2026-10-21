"use client";
// Each source's own mark, wherever a source shows (Documents, search, Ask's sources, the gallery, connections), so people
// can tell at a glance where something came from: the brand's logo from public/brand/, which the web image fetches as
// it builds (scripts/brand-icons.mjs) and Aerugo serves itself, so no page calls a third party. Wherever a logo isn't
// there, a neutral glyph stands in. Aerugo never draws a brand's logo itself.
import { createElement as h, useState } from "react";
import { Icon } from "./icons.js";

// A source's kind (as documents and connections name it) to its mark's file in public/brand/.
export const BRANDS = {
  github: "github", gdrive: "googledrive", google_drive: "googledrive", google: "googledrive", confluence: "confluence",
  jira: "jira", notion: "notion", slack: "slack", okta: "okta", directory: "okta", microsoft: "microsoftsharepoint",
  teams: "microsoftteams", lineage: "apacheairflow", catalog: "googlecloud", dataplex: "googlecloud", unity: "databricks", glue: "awsglue",
  purview: "microsoftpurview", datahub: "datahub",
};
const GLYPHS = { github: "code", pipelines: "refresh", lineage: "map", imports: "draft" }; // where there's no logo
const NAMES = {
  github: "GitHub", gdrive: "Google Drive", google_drive: "Google Drive", google: "Google Drive", confluence: "Confluence",
  jira: "Jira", notion: "Notion", slack: "Slack", okta: "Okta", directory: "Okta", microsoft: "Microsoft 365",
  teams: "Microsoft Teams", okf: "Knowledge page", upload: "Upload", pipeline: "Pipeline", import: "Import", catalog: "Data catalog", dataplex: "Dataplex",
  unity: "Unity Catalog", glue: "AWS Glue", purview: "Microsoft Purview", datahub: "DataHub",
};

// A source's name as people know it, the same wherever it shows.
export const sourceName = (kind) => NAMES[kind] || (kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "Source");

export function SourceIcon({ kind, label = "", size = 14 }) {
  const [missing, setMissing] = useState(false);
  const file = BRANDS[kind];
  if (!file || missing) {
    return h("span", { className: "source-icon", role: label ? "img" : undefined, "aria-label": label || undefined, "aria-hidden": label ? undefined : true },
      h(Icon, { name: GLYPHS[kind] || "docs", size }));
  }
  return h("img", { className: "source-icon", src: `/brand/${file}.svg`, width: size, height: size, alt: label,
                    loading: "lazy", decoding: "async", onError: () => setMissing(true) });
}
