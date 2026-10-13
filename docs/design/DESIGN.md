# DESIGN.md: the Aerugo interface

What the product looks like and why, for anyone changing the web app. The tokens here are the source of truth: `services/web/app/globals.css` implements them, and `docs/design/screens.md` applies them screen by screen.

## Lineage: Datavine's system, Aerugo's accent

Slice D1 rebuilt the interface on the design system of **Datavine**, an analytics platform built earlier by the same author (its `frontend/DESIGN.md`): the calm and density of enterprise data platforms such as Snowflake, Databricks, and Linear. Three of its rules carry over as Aerugo's own:

1. **Calm surface hierarchy.** Few colors, strong type. A card only when the card itself is the interaction.
2. **Utility language.** Labels say what an area is, or what someone can do there; never mood.
3. **Trust is earned at the pixel level.** Every element builds or erodes confidence in the knowledge behind it.

What stays Aerugo's: **Verdigris** as the one accent, the tiers by depth of Verdigris, copper for entities, and everything below about voice and disclosure.

## Brand

**What it is.** A knowledge platform that answers questions from a company's own documents, always within each person's permissions, and always showing its sources.

**Who uses it, from first day to daily driver.**
- *Readers* (most people): search, ask, read. They should never meet a setting.
- *Team admins*: bring knowledge in (connections, imports), manage members and models, review flagged personal data.
- *Org admins and compliance owners*: retention, legal holds, people's data, access reviews, platform settings.

**Voice.** Plain verbs, sentence case, no jargon on default views. Name things by what people do ("Import an export", "Remove sign-in"), never by how the system is built ("upload artifact", "delete OAuth client"). An action keeps its name through the flow.

**Reading is where the care goes.** Answers and concept pages are set a size up from the interface, at a comfortable measure, and citation numbers carry the accent, like a reference work's. Everything around them stays quiet and dense.

## Theme: light, dark, or the system's

Light is the default, because the core job is reading long passages and their sources, compliance views end up in audits, screenshots, and printouts, and office daylight is the common setting. **Dark** (slice D2) follows Datavine's: a warm charcoal canvas with no blue tint, cards lighter than the canvas, a sidebar darker than it, and status colors lifted for a dark ground.

**Choosing.** The account menu, at the top bar's end, offers Light, Dark, and System. The choice is a cookie (`aerugo-theme`), so the server renders the page in it from the first paint: no flash, and no inline script. Light and Dark set `data-theme` on `<html>`; System sets nothing, and the stylesheet's `prefers-color-scheme` media query follows the operating system, live.

**One set of tokens, two values.** Every color in the interface, the map's canvas included, is a token: the map reads its six colors (links, tiers, the paper between bubbles, rings, and labels) from the same tokens, and repaints when the theme changes. Nothing outside the token blocks carries a color.

## Color tokens

| Token | Value | Role |
|---|---|---|
| `--bg` | `#F4F6F8` | Canvas: the page behind everything |
| `--surface` | `#FFFFFF` | Cards, panels, inputs, dialogs: they lift off the canvas |
| `--surface-2` | `#F1F3F6` | Inset: table heads, code, quiet wells |
| `--tint` / `--tint-strong` | `#EEF1F5` / `#E3E8EE` | Hover; pressed |
| `--border` / `--border-strong` | `#DDE1E7` / `#C5CCD6` | Rules and panel edges; hovered controls |
| `--fg` | `#121820` | Text and headings |
| `--fg-2` | `#3A4552` | Secondary prose: snippets, excerpts, dialog bodies |
| `--muted` | `#5F6B7A` | Labels, meta, inactive navigation |
| `--faint` | `#646F7C` | Placeholders and hints |
| `--accent` | `#1F6F61` | Verdigris: primary actions, focus rings, where you are, citation numbers |
| `--accent-hover` / `--accent-press` | `#185C51` / `#134A41` | Primary button states; text on the accent tint |
| `--accent-tint` | `rgba(31, 111, 97, 0.1)` | Icon tiles, the selected row |
| `--on-accent` | `#FFFFFF` | Text on the accent |
| `--success` / `--warning` / `--danger` | `#177A45` / `#9A5B00` / `#B3261E` | Status, after Datavine's six: done, needs attention, failed |
| `--info` / `--pending` / `--neutral` | `#0A66C2` / `#6B35DB` / `#5F6B7A` | Status: for your information, in progress, nothing to say |
| `--danger-hover` | `#8F1F18` | Destructive confirm buttons |
| `--gold` / `--silver` / `--evidence` | `#13403A` / `#2F7F6F` / `#7D8A99` | Tiers by depth of Verdigris: the darker, the more reviewed; evidence is neutral |
| `--entity` | `#A4572A` | Copper, on the map only: people, systems, and teams named in documents |
| `--sb-bg` / `--sb-border` | `#EAEEF2` / `#D8DDE4` | The sidebar: a shade darker than the canvas (Datavine's explorer pattern) |
| `--sb-hover` / `--sb-active` | `#DFE5EC` / `#D6DEE8` | Its hovered and current items |
| `--sb-fg` / `--sb-muted` | `#121820` / `#4F5B69` | Its text |

Also in both themes: `--map-bg` and `--map-border` (the map's paper), a tint and a rule for each alert status (`--danger-tint`, `--danger-border`, and the same for success and warning), `--on-danger`, `--bar-bg` (the translucent top bar), `--scrim` (behind dialogs and the drawer), `--selection`, and `--shadow-float` and `--shadow-rail`.

Every status reads as text as well as a dot, so status is never color alone.

**Contrast, measured** (WCAG 2.1 relative luminance; AA needs 4.5:1 for text and 3:1 for meaningful graphics). Datavine's own muted gray, `#6C7A8A`, is 4.4:1 on white, just under AA, so Aerugo's muted and faint are a little darker: every pairing below passes.

| Pairing | Ratio | Needs |
|---|---|---|
| `--fg` on `--surface` / `--bg` / the sidebar / its current item | 17.84 / 16.47 / 15.30 / 13.14 | 4.5 |
| `--fg-2` on `--surface` / `--bg` | 9.75 / 9.00 | 4.5 |
| `--muted` on `--surface` / `--bg` / `--surface-2` / `--tint` | 5.43 / 5.01 / 4.88 / 4.79 | 4.5 |
| `--faint` on `--surface` / `--surface-2` / `--bg` | 5.11 / 4.60 / 4.72 | 4.5 |
| `--sb-muted` on the sidebar / a hovered item / the current item | 5.94 / 5.46 / 5.10 | 4.5 |
| `--accent` text on `--surface` / `--bg` / `--tint` | 5.99 / 5.53 / 5.29 | 4.5 |
| A tile's initials (`--accent-hover`) on the accent tint, over the sidebar / white | 5.86 / 6.78 | 4.5 |
| White on `--accent` / `--accent-hover` / `--accent-press` / `--danger` | 5.99 / 7.81 / 10.08 / 6.54 | 4.5 |
| Status text on `--surface`: success / warning / danger / info / pending | 5.38 / 5.43 / 6.54 / 5.69 / 6.65 | 4.5 |
| Status text on `--bg`: success / warning / danger / info | 4.96 / 5.01 / 6.03 / 5.25 | 4.5 |
| Tier and entity dots on white: gold / silver / evidence / entity | 11.52 / 4.79 / 3.52 / 5.28 | 3.0 |
| The focus ring (`--accent`) against `--bg` | 5.53 | 3.0 |

### Dark

| Token | Value | | Token | Value |
|---|---|---|---|---|
| `--bg` | `#1A1C22` | | `--accent` / hover / press | `#5CC2AE` / `#7AD2C0` / `#4AAE9B` |
| `--surface` / `--surface-2` | `#212530` / `#262A33` | | `--on-accent` | `#08201B` |
| `--tint` / `--tint-strong` | `#2A2E38` / `#323744` | | `--success` / `--warning` / `--danger` | `#5AC58D` / `#E0A24E` / `#F07B70` |
| `--border` / `--border-strong` | `#363A44` / `#4A4F5C` | | `--info` / `--pending` | `#6CB0F2` / `#A98CF2` |
| `--fg` / `--fg-2` | `#E0E3E8` / `#C4C9D1` | | `--on-danger` | `#1F0A08` |
| `--muted` / `--faint` | `#9AA3AF` / `#929BA7` | | `--gold` / `--silver` / `--evidence` | `#9BDCCB` / `#5FB3A2` / `#7D8A99` |
| The sidebar, its hover, its current item | `#131519` / `#1E2128` / `#252A33` | | `--entity` | `#DC955F` |
| `--map-bg` | `#16181D` | | | |

Two things turn around on dark, by measurement rather than taste. **Verdigris gets lighter and carries dark text**: white on any Verdigris light enough to read on charcoal fails AA (the trap Datavine's own dark blue falls into), and so does white on a readable red, so destructive buttons carry dark text too. **Gold is the brightest tier**: the more reviewed, the more prominent, by brightness instead of depth, and evidence stays the quiet neutral.

| Pairing | Ratio | Needs |
|---|---|---|
| `--fg` on `--surface` / `--bg` / the sidebar / its current item | 11.90 / 13.24 / 14.21 / 11.20 | 4.5 |
| `--fg-2` on `--surface` / `--bg` | 9.20 / 10.24 | 4.5 |
| `--muted` on `--surface` / `--bg` / `--surface-2` / `--tint` | 6.00 / 6.68 / 5.63 / 5.32 | 4.5 |
| `--faint` on `--surface` / `--surface-2` / `--tint` | 5.44 / 5.11 / 4.83 | 4.5 |
| `--sb-muted` on the sidebar / a hovered item / the current item | 7.16 / 6.32 / 5.65 | 4.5 |
| `--accent` text on `--surface` / `--bg` / `--tint` | 7.13 / 7.93 / 6.33 | 4.5 |
| A tile's initials (`--accent-hover`) on the accent tint, over the sidebar | 8.03 | 4.5 |
| `--on-accent` on `--accent` / `--accent-hover` / `--accent-press`; `--on-danger` on `--danger` | 7.94 / 9.58 / 6.34 / 7.03 | 4.5 |
| Status text on `--surface`: success / warning / danger / info / pending | 7.15 / 6.89 / 5.66 / 6.65 / 5.63 | 4.5 |
| Tiers on the map's paper: gold / silver / evidence / entity | 11.41 / 7.15 / 5.05 / 7.19 | 3.0 |
| The focus ring (`--accent`) against `--bg` | 7.93 | 3.0 |

The first dark draft had one miss, `--faint` on a hovered row at 4.37:1; `--faint` was raised to `#929BA7`, still dimmer than `--muted`.

## Typography

| Role | Family | Why |
|---|---|---|
| Everything: the interface and reading | **Inter**, with its cleaner letterforms (`cv02 cv03 cv04 cv11 ss01`) | Datavine's choice: dense tables stay readable, and one family keeps the page calm |
| Code: principals (`group:payments`), document ids, raw OKF | **JetBrains Mono** | Only for text that really is code. Never for numbers or decorative labels |

Both are self-hosted at build time by `next/font`, so there are no requests to Google at runtime. **Numbers line up**: every table cell, code span, and key hint uses tabular figures.

**Scale** (13px base, Datavine's enterprise density):

| Step | Size / line height | Weight | Use |
|---|---|---|---|
| Page title (h1) | 18 / 24, −0.02em | 600 | One per page |
| Panel title (h2) | 14 / 20, −0.01em | 600 | Panel and dialog headings |
| Subhead (h3) | 13 / 18 | 600 | Rare |
| Body | 13 / 20 | 400 | Everything in the interface |
| Small | 12 / 16 | 400–500 | Compact buttons, meta rows, breadcrumbs, hints |
| Label | 10–11 / 16, +0.04–0.07em, capitals | 600 | Table headers and sidebar groups only |
| Reading | 15 / 26 | 400 | Answers and concept prose, at most 68 characters wide |

Rules: sentence case everywhere; capitals only for the labels above; never color or italicize one word of a headline.

## Spacing, shape, depth

- **Base unit: 4px.** Page padding 24px at the top and 28px at the sides on desktop, 16px on phones; 20px under a page's head; 16px inside panels.
- **Radius.** 6px for controls, 8px for panels, cards, and dialogs; 0 for single-sided accents (the current item's bar, section tabs).
- **Depth.** Canvas, then card, then inset, then hover: background shifts and 1px borders do the layering. **Shadows only on what floats above the page**: the open rail, menus, the palette, and dialogs. The sticky top bar is translucent, with a light blur. No gradients, no glows.
- **Nesting.** At most two container levels: a page holds panels, and a panel holds rows. Never a card inside a card.
- **Motion.** Only in answer to an action: 150ms color and border transitions, dialogs appearing, and the rail, which opens 80ms after you reach it and closes 250ms after you leave, so passing over it doesn't flicker. `prefers-reduced-motion` turns animation off entirely.

## Layout and breakpoints

| Width | Shell | Content |
|---|---|---|
| ≥ 900px | A 52px rail of icons that opens to 232px over the page on hover or keyboard focus, and a 44px sticky top bar | One left-aligned column, at most 1200px (reading pages 800px); tables at full width |
| < 900px | The top bar gains a menu button; the sidebar becomes a 300px drawer over a dimmed backdrop, closed by a tap outside or Escape, with 40px touch targets | 16px padding; tables scroll inside their panel, never the page; palette hints hide |

## The shell

- **Sidebar**, top to bottom: the mark and the name; **the scope**, after Datavine's project selector (a tile with the team's initials, or "All" for the whole org, then its name and the org it's in); **the pages, grouped** (Knowledge: Documents, Map, Search; Assist: Ask, Proposals; then Team settings in a team, or Admin at the org); and at the foot, Developer, Connected accounts, and the ⌘K hint. The current page gets a tinted row, a 3px accent bar on its left edge, and an accent icon.
- **Top bar**: the breadcrumb (the org, the team, the page; none on the org's Home), search (⌘K), the API's health in words, and your account (who you are, your teams, the keyboard shortcuts, signing out).

## Components and their rules

Every interactive element has designed **hover**, **focus-visible** (a 2px accent ring with a 2px offset, or inset in dense lists), **active**, and **disabled** (50% opacity, `not-allowed` cursor, no hover change) states.

| Component | Rule |
|---|---|
| Button, primary | Accent fill, white text. **One per screen**, for the action most people came to do. |
| Button, secondary | Card fill, 1px border. Everything else. |
| Button, ghost | No fill until hovered. Row actions and dismissals. |
| Button, destructive | Only inside a confirmation dialog: danger fill, white text. On the page, a destructive action is a ghost or secondary button that *opens* the dialog. |
| Settings exception | On settings sections, each independent setting keeps its own Save. It stays secondary and disabled until its form has something to save, then becomes primary. |
| Section tabs (`SectionNav`) | Links, not buttons: each section has its own address. The current one gets `aria-current="page"` and a 2px accent underline. |
| Status | A dot plus a word ("Importing…", "Set up", "Failed: why"), in one of the six status colors. Never a filled pill, never a blink. |
| Data table | Capital 11px headers on an inset head, 34px rows, a tint on hover, numbers right-aligned in tabular figures. |
| Card | Only when the card itself is the interaction (it opens something). Never a decorative grid of identical cards. |
| Data views | Four mandatory states: **loading** (a skeleton shaped like the real rows), **empty** (one sentence on what to do next), **error** (what happened, plus Retry), **success** (inline status beside the action that caused it). |
| Modal | Only for confirmations: anything destructive or irreversible. Focus starts on Cancel; Escape cancels; Tab stays inside; focus returns afterward. |
| Command palette | The universal power layer: ⌘K or Ctrl K from anywhere. It lists only what the person can use, matches letters in order, and turns anything typed into a search. |
| Breadcrumbs | In the top bar on every page but the org's Home: `acme › Payments › Documents`. |
| Keyboard | `/` search · `G` then `H M S Q D P A C V L` to go somewhere · `?` the shortcut list · `Esc` closes. Shortcuts never fire while typing in a field. |

## Disclosure: three levels on every screen

1. **Default**: one primary action, no jargon, only what a first-time person needs.
2. **Expanded**: secondary actions, filters, and context, revealed in place by a visible control (a section tab, a "Show resolved" toggle, "IDs and Terraform"), never hidden behind an unlabeled menu.
3. **Power**: the palette, shortcuts, raw data (the "Raw OKF" disclosure on concepts), and bulk or advanced settings.

**Depth limit:** any action is at most two navigation levels from any screen (a sidebar item, then a section tab). With the palette, it's one step.

## Anti-patterns to avoid

- Gradients, glows, or shadows on anything that doesn't float above the page.
- A second accent color, or a bright cyan anywhere.
- Filled status pills or animated status blinks; status shown by color alone.
- Decorative card grids, three-column feature grids, or identical cards for everything.
- Icon soup: an icon goes beside a label, never instead of one (the rail's icons carry their names, shown when it opens and always announced).
- Capitals outside table headers and sidebar groups; arrows appended to links.
- Monospace for anything that isn't code.
- More than one primary button on a screen; destructive actions without a confirmation.
- Settings buried behind unlabeled overflow menus; more than two levels of navigation to any action.
- Generic copy ("Submit", "Click here", "Error occurred").

## Assumptions

- **Tailwind.** `screens.md` gives reference markup in Tailwind, mapped to these tokens by the theme below. The shipped app keeps its dependency-free stylesheet with the same tokens, because adding Tailwind means a new build dependency for no change on screen.
- **Platform.** Keyboard shortcuts show ⌘ on Apple devices and Ctrl elsewhere.
- **Primary action on read-only views.** The audit trail and access-review results have no primary button: their job is reading, and a fake call to action would be noise.

## Tailwind theme (for the reference markup)

```js
// tailwind.config.js: the tokens above, as utilities (bg-canvas, text-fg, border-rule, ring-accent, ...)
export default {
  theme: {
    extend: {
      colors: {
        canvas: "#F4F6F8", card: "#FFFFFF", inset: "#F1F3F6", tint: { DEFAULT: "#EEF1F5", strong: "#E3E8EE" },
        rule: { DEFAULT: "#DDE1E7", strong: "#C5CCD6" },
        fg: { DEFAULT: "#121820", 2: "#3A4552" }, muted: "#5F6B7A", faint: "#646F7C",
        accent: { DEFAULT: "#1F6F61", hover: "#185C51", press: "#134A41" },
        ok: "#177A45", warn: "#9A5B00", bad: { DEFAULT: "#B3261E", hover: "#8F1F18" }, info: "#0A66C2", pending: "#6B35DB",
        sidebar: { DEFAULT: "#EAEEF2", rule: "#D8DDE4", hover: "#DFE5EC", active: "#D6DEE8", muted: "#4F5B69" },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["var(--font-mono)", "JetBrains Mono", "ui-monospace", "monospace"],
      },
      fontSize: { base: ["13px", "20px"] },
      borderRadius: { control: "6px", panel: "8px" },
    },
  },
};
```
