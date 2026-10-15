// Markdown, rendered safely: Ask's answers (slice K9) and documents read in their space. Paragraphs, bullet and
// numbered lists, headings, quotes, tables (pipe tables, with alignment), fenced code, bold, italics, and inline code. Everything is built as elements,
// never as HTML strings, so nothing a source or a model writes can inject markup. Citation markers like [1] become
// superscript numbers.
//
// For documents, { media: true } also renders links (http and https only), images (https), and embedded video: a
// YouTube, Vimeo, or Loom link alone on its line becomes its player, built from the video's id alone (YouTube's
// privacy-enhanced domain), and a video file's link becomes a video. Answers never embed anything.
import { createElement as h } from "react";

const INLINE = /(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\[\d{1,2}\]|\*[^*\s][^*\n]*\*|(?<![\w])_[^_\s][^_\n]*_(?![\w]))/g;
const LINK = /\[([^\]\n]{1,200})\]\((https?:\/\/[^\s)]{1,2000})\)/;  // [text](https://...), for documents
const INLINE_LINKS = new RegExp(`(${LINK.source}|${INLINE.source.slice(1, -1)})`, "g");
const ID = /^[A-Za-z0-9_-]{6,20}$/;

// A video page's player address, built from the video's id alone; null for anything else.
export function videoEmbed(url) {
  let u;
  try { u = new URL(url); } catch { return null; }
  if (u.protocol !== "https:") return null;
  const host = u.hostname.replace(/^(www|m)\./, "");
  const parts = u.pathname.split("/").filter(Boolean);
  if (host === "youtube.com") {
    const id = parts[0] === "watch" ? u.searchParams.get("v") : ["shorts", "embed", "live"].includes(parts[0]) ? parts[1] : null;
    return ID.test(id || "") ? `https://www.youtube-nocookie.com/embed/${id}` : null;
  }
  if (host === "youtu.be") return ID.test(parts[0] || "") ? `https://www.youtube-nocookie.com/embed/${parts[0]}` : null;
  if (host === "vimeo.com") return /^\d{4,12}$/.test(parts[0] || "") ? `https://player.vimeo.com/video/${parts[0]}` : null;
  if (host === "loom.com" && ["share", "embed"].includes(parts[0]) && /^[a-f0-9]{16,40}$/.test(parts[1] || "")) return `https://www.loom.com/embed/${parts[1]}`;
  return null;
}

// A link to a video file Aerugo can play itself (https only).
export function videoFile(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" && /\.(mp4|webm|ogg|mov)$/i.test(u.pathname) ? u.href : null;
  } catch { return null; }
}

function player(src, title, key) {
  return h("div", { key, className: "doc-embed" }, h("iframe", {
    src, title: title || "Embedded video", loading: "lazy", allowFullScreen: true,
    allow: "encrypted-media; picture-in-picture; fullscreen", referrerPolicy: "strict-origin-when-cross-origin",
    sandbox: "allow-scripts allow-same-origin allow-presentation allow-popups" }));
}

// A paragraph that's only a video, an image, or a link becomes that thing, in documents.
function media(line, key) {
  const image = /^!\[([^\]\n]{0,200})\]\((https?:\/\/[^\s)]{1,2000})\)$/.exec(line);
  if (image && image[2].startsWith("https:")) return h("figure", { key, className: "doc-figure" },
    h("img", { src: image[2], alt: image[1], loading: "lazy", referrerPolicy: "no-referrer" }), image[1] ? h("figcaption", null, image[1]) : null);
  if (image) return h("p", { key }, h("a", { href: image[2], target: "_blank", rel: "noreferrer noopener" }, image[1] || image[2]));  // never loads itself
  const linked = new RegExp(`^${LINK.source}$`).exec(line);
  const url = linked ? linked[2] : (/^<?(https?:\/\/\S{1,2000}?)>?$/.exec(line) || [])[1];
  if (!url) return null;
  const embed = videoEmbed(url);
  if (embed) return player(embed, linked ? linked[1] : null, key);
  const file = videoFile(url);
  if (file) return h("video", { key, className: "doc-video", src: file, controls: true, preload: "metadata" });
  return h("p", { key }, h("a", { href: url, target: "_blank", rel: "noreferrer noopener" }, linked ? linked[1] : url));
}
// A pipe table: a header row, a divider row of dashes (colons for alignment), then body rows, as GitHub writes them.
const DIVIDER = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const isRow = (line) => /\|/.test(line || "") && !/^\s*```/.test(line);
const startsTable = (lines, i) => isRow(lines[i]) && i + 1 < lines.length && DIVIDER.test(lines[i + 1]) && !DIVIDER.test(lines[i]);
function cells(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, "|").trim());
}
const alignOf = (cell) => (/^:-+:$/.test(cell) ? "center" : /-:$/.test(cell) ? "right" : null);
const BULLET = /^\s*[-*+]\s+/;
const NUMBER = /^\s*(\d{1,3})[.)]\s+/;
const STARTS_BLOCK = /^(\s*#{1,6}\s|\s*```|\s*[-*+]\s+|\s*\d{1,3}[.)]\s+|\s*>)/;

// Bold, italics, inline code, and citation markers inside one line of text; in documents, links too.
export function inline(text, key = "i", links = false) {
  const out = [];
  let last = 0;
  let n = 0;
  for (const m of String(text).matchAll(links ? INLINE_LINKS : INLINE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    const k = `${key}-${n++}`;
    const link = links ? LINK.exec(t) : null;
    if (link && link.index === 0 && link[0] === t) out.push(h("a", { key: k, href: link[2], target: "_blank", rel: "noreferrer noopener" }, link[1]));
    else if (t.startsWith("**") || t.startsWith("__")) out.push(h("strong", { key: k }, inline(t.slice(2, -2), k, links)));
    else if (t.startsWith("`")) out.push(h("code", { key: k }, t.slice(1, -1)));
    else if (t.startsWith("[")) out.push(h("sup", { key: k, className: "cite-n" }, t.slice(1, -1)));
    else out.push(h("em", { key: k }, inline(t.slice(1, -1), k, links)));
    last = m.index + t.length;
  }
  if (last < String(text).length) out.push(String(text).slice(last));
  return out;
}

export function markdown(text, { media: rich = false } = {}) {
  const say = (t, k) => inline(t, k, rich);
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = blocks.length;
    if (!line.trim()) { i++; continue; }
    if (/^\s*```/.test(line)) { // fenced code, kept exactly as written
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      blocks.push(h("pre", { key }, h("code", null, body.join("\n"))));
      continue;
    }
    if (startsTable(lines, i)) {
      const head = cells(lines[i]);
      const aligns = cells(lines[i + 1]).map(alignOf);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() && isRow(lines[i])) rows.push(cells(lines[i++]));
      const cell = (tag, text, j, k) => h(tag, { key: j, style: aligns[j] ? { textAlign: aligns[j] } : undefined }, say(text || "", k));
      blocks.push(h("div", { key, className: "md-table" }, h("table", null,
        h("thead", null, h("tr", null, head.map((c, j) => cell("th", c, j, `t${key}-h${j}`)))),
        rows.length ? h("tbody", null, rows.map((r, ri) => h("tr", { key: ri }, head.map((_, j) => cell("td", r[j], j, `t${key}-${ri}-${j}`))))) : null)));
      continue;
    }
    const heading = /^\s*(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (heading) {
      blocks.push(h(heading[1].length <= 2 ? "h4" : "h5", { key }, say(heading[2], `h${key}`)));
      i++;
      continue;
    }
    if (BULLET.test(line) || NUMBER.test(line)) {
      const ordered = NUMBER.test(line);
      const marker = ordered ? NUMBER : BULLET;
      const start = ordered ? Number(NUMBER.exec(line)[1]) : 1;
      const items = [];
      while (i < lines.length && marker.test(lines[i])) {
        let item = lines[i++].replace(marker, "");
        while (i < lines.length && lines[i].trim() && !STARTS_BLOCK.test(lines[i])) item += ` ${lines[i++].trim()}`; // wrapped
        items.push(h("li", { key: items.length }, say(item, `l${key}-${items.length}`)));
        while (i < lines.length && !lines[i].trim() && i + 1 < lines.length && marker.test(lines[i + 1])) i++; // loose list
      }
      blocks.push(h(ordered ? "ol" : "ul", ordered && start !== 1 ? { key, start } : { key }, items));
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quoted = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quoted.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push(h("blockquote", { key }, say(quoted.join(" "), `q${key}`)));
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !(para.length && (STARTS_BLOCK.test(lines[i]) || startsTable(lines, i)))) para.push(lines[i++].trim());
    const special = rich && para.length === 1 ? media(para[0], key) : null;
    blocks.push(special || h("p", { key }, say(para.join(" "), `p${key}`)));
  }
  return blocks;
}
