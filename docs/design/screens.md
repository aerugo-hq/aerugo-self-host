# Screens, disclosure, and navigation

Every screen in the web app: what it's for, its one primary action, and what each disclosure level reveals (see DESIGN.md for the three levels). Then the navigation map, and reference markup for each default view in Tailwind classes, using the theme at the end of DESIGN.md. **Built** means it ships in the app today; **specified** means it's designed here and comes in a later pass.

## Inventory

| Screen | Address | Who | Primary action | Secondary (visible, subordinate) | Expanded (L2) | Power (L3) |
|---|---|---|---|---|---|---|
| Map | `#/map` | Everyone | Find a concept on the map (search box) | Team filter, zoom to fit | Replay at a past date; tier and team legend; a concept's neighbors on hover | ⌘K; raw graph totals |
| Search | `#/search[/<words>]` | Everyone | Search (one box) | Scope (this team or the whole org); the Ask tab leads to Ask | "Mark as the right answer" on hover | `/` from anywhere; ⌘K "Search for …" |
| Ask | `#/ask[/<conversation>]` | Everyone | Ask (the composer) | Your history (grouped by day, searchable over your own questions), model and scope, Delete (confirmed) | An answer's Sources and its Graph; a node's panel with Ask about this and Open | `G Q`; ⌘K "Ask" |
| Concept | `#/concepts/<id>` | Its readers | Read it | Open the source | History timeline, related concepts, sources | Raw OKF disclosure; copy id |
| Proposals | `#/proposals[/<id>]` | Curators, admins | Draft from a topic | Filter by state | Draft beside its sources; PR timeline | ⌘K |
| Alerts | `#/alerts` | Admins | Act on the firing alert | Acknowledge | Alert history; webhook destination | ⌘K "Alerts" |
| Admin, Sources | `#/admin/sources` | Team admins | **Import** an export | Re-sync a connection | Each repo's team (Repos); run results | ⌘K "Import a Confluence or Notion export"; `G A` |
| Admin, People | `#/admin/people` | Team admins | Add a member | Remove a role | Create a team (org admins) | ⌘K "Admin: People" |
| Admin, Models | `#/admin/models` | Team admins | Add a model | Edit, rotate key | Budget and this month's tokens (org admins edit) | ⌘K |
| Admin, Review | `#/admin/review` | Team admins | Decide each flag (Make … / Dismiss, both secondary) | Show resolved | Kinds and counts per flag (never values) | ⌘K |
| Admin, Activity | `#/admin/activity` | Team admins | none: a read-only trail | Filter by action | Event details (drawer, *specified*) | ⌘K |
| Compliance, Retention | `#/compliance/retention` | Org admins | Add a policy | Run now | Recent runs per policy | `G C` |
| Compliance, Legal holds | `#/compliance/holds` | Org admins | Place a hold | Release | Held documents | ⌘K |
| Compliance, People's data | `#/compliance/people` | Org admins | Export a person's data | Erase (behind a typed confirmation) | What an export holds | ⌘K |
| Compliance, Access review | `#/compliance/access` | Org admins | Run the review | Download JSON | Per-team breakdown | ⌘K |
| Compliance, Platform | `#/compliance/platform` | Org admins | Save, per setting (settings exception) | Remove (confirmed) | Rate limits, reranker, Google sign-in | ⌘K |
| Connected accounts | `#/accounts` | Everyone | Link GitHub | Link Google (once set up); Unlink | Verified-by detail | `G L` |
| Command palette | overlay | Everyone | Run the highlighted command | Arrow keys, mouse | Shortcut list (`?`) | It *is* the power layer |
| Confirmation | overlay | Anyone removing something | Cancel (focused first) | The destructive confirm | n/a | Esc cancels |

Status: everything above is **built**, except the drawers (Activity event details; a search result preview), Ask in the palette, and the Map legend's replay shortcut, which are **specified**.

## Navigation map

A novice reaches every feature in at most three taps (sidebar, section, action). A power user reaches the same place in one step: one palette command, or one shortcut.

| Feature | Novice path (taps) | Power path (one step) |
|---|---|---|
| Search everything | Sidebar "Search and ask", type, Enter (2) | `/`, or ⌘K then type, then "Search for …" |
| Ask a question | Sidebar "Search and ask", Ask tab, type (3) | ⌘K "Search and ask" (Ask in the palette is *specified*) |
| Read a concept | Search result, or a map node (1–2) | ⌘K "Search for …" |
| Draft a proposal | Sidebar "Proposals", "Draft from a topic" (2) | `G P` |
| Import an export | Sidebar "Admin" (Sources is the default), "Import" (2) | ⌘K "Import a Confluence or Notion export" |
| Add a member | Sidebar "Admin", tab "People", "Add" (3) | ⌘K "Admin: People" |
| Add a model or budget | Sidebar "Admin", tab "Models", "Add model" (3) | ⌘K "Admin: Models" |
| Review personal data | Sidebar "Admin", tab "Review" (2) | ⌘K "Admin: Review" |
| Read the audit trail | Sidebar "Admin", tab "Activity" (2) | ⌘K "Admin: Activity" |
| Retention policy | Sidebar "Compliance" (Retention is the default), "Add policy" (2) | `G C` |
| Legal hold | Sidebar "Compliance", tab "Legal holds", "Place hold" (3) | ⌘K "Compliance: Legal holds" |
| Export or erase a person's data | Sidebar "Compliance", tab "People's data" (2) | ⌘K "Compliance: People's data" |
| Access review | Sidebar "Compliance", tab "Access review", "Run" (3) | ⌘K "Compliance: Access review" |
| Rate limits, reranker, Google sign-in | Sidebar "Compliance", tab "Platform" (2) | ⌘K "Compliance: Platform" |
| Link GitHub or Google | Sidebar "Connected accounts", "Link …" (2) | `G L` |
| Alerts | Sidebar "Alerts" (1) | ⌘K "Alerts" |
| Every shortcut | Palette footer hint (2) | `?` |

## Reference markup (default views)

Tailwind classes, using the theme in DESIGN.md. Comments mark where the expanded (L2) and power (L3) layers attach. Every interactive element carries hover, `focus-visible`, active, and disabled classes from the button recipes in the shell section.

### Shell (every page)

```html
<!-- Desktop: sidebar + content. Below 900px the sidebar becomes a drawer behind the top bar's menu button. -->
<div class="grid min-h-screen grid-cols-1 bg-paper font-sans text-[14px] leading-5 text-ink lg:grid-cols-[232px_minmax(0,1fr)]">
  <header class="flex h-12 items-center gap-2 border-b border-rule bg-sheet px-4 lg:hidden">
    <span aria-hidden="true" class="grid h-6 w-6 place-items-center rounded-control bg-ink text-[11px] font-semibold text-sheet">kl</span>
    <b class="flex-1">acme</b>
    <button type="button" aria-label="Search or jump to a page" class="h-8 w-8 rounded-control text-muted hover:bg-tint hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent active:bg-tint-strong">…</button>
    <button type="button" aria-label="Open menu" aria-expanded="false" class="h-8 w-8 rounded-control text-muted hover:bg-tint">…</button>
  </header>
  <aside aria-label="Navigation" class="hidden flex-col gap-4 border-r border-rule bg-paper p-3 lg:flex">
    <!-- L3: the palette. The button says what it does; the shortcut is shown, not required. -->
    <button type="button" class="flex h-8 items-center gap-2 rounded-control border border-rule bg-sheet px-2 text-muted hover:border-rule-strong focus-visible:outline-2 focus-visible:outline-accent">
      <span class="flex-1 text-left">Search or jump to</span><kbd class="rounded border border-rule px-1.5 font-mono text-[11px]">⌘K</kbd>
    </button>
    <nav class="flex flex-col gap-0.5">
      <a href="#/map" aria-current="page" class="flex h-8 items-center gap-2 rounded-control px-2 font-medium text-ink aria-[current=page]:bg-tint hover:bg-tint">Map</a>
      <a href="#/search" class="flex h-8 items-center gap-2 rounded-control px-2 text-muted hover:bg-tint hover:text-ink">Search and ask</a>
      <a href="#/proposals" class="… same …">Proposals</a>
      <!-- Admins only: --> <a href="#/alerts" class="…">Alerts</a> <a href="#/admin" class="…">Admin</a>
      <!-- Org admins only: --> <a href="#/compliance" class="…">Compliance</a>
    </nav>
    <div class="mt-auto"><a href="#/accounts" class="…">Connected accounts</a><!-- account row, Sign out --></div>
  </aside>
  <main class="min-w-0 px-4 py-6 lg:px-8 lg:py-8"><!-- one view at a time --></main>
</div>

<!-- Button recipes (every state designed):
  primary:     h-8 px-3 rounded-control bg-accent text-white font-medium hover:bg-accent-hover active:bg-accent-press
               focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50 disabled:cursor-not-allowed
  secondary:   h-8 px-3 rounded-control border border-rule bg-sheet font-medium hover:bg-tint hover:border-rule-strong active:bg-tint-strong …
  ghost:       h-8 px-3 rounded-control text-muted hover:bg-tint hover:text-ink …
  destructive: (dialogs only) bg-bad text-white hover:bg-bad-hover … -->
```

### Map (`#/map`)

```html
<section class="flex h-screen flex-col px-4 py-6 lg:px-8">
  <div class="mb-4 flex items-end justify-between gap-6">
    <div><h1 class="text-[20px] font-semibold leading-7">Map</h1>
      <p class="mt-1 text-muted">How your teams' knowledge connects, limited to what you can see.</p></div>
    <!-- L2: totals (concepts, documents, links) as a dl, right-aligned, tabular numbers -->
  </div>
  <div class="flex min-h-0 flex-1 flex-col rounded-panel border border-rule bg-sheet">
    <div class="flex flex-wrap items-center gap-3 border-b border-rule p-2">
      <!-- Primary: find a concept --> <input type="search" aria-label="Find on the map" placeholder="Find a concept or document" class="h-8 w-60 rounded-control border border-rule px-2.5 placeholder:text-faint focus-visible:outline-2 focus-visible:outline-accent">
      <!-- L2: team filter (segmented control), replay date, legend toggle -->
    </div>
    <div role="img" aria-label="Knowledge map" class="min-h-0 flex-1"><!-- canvas --></div>
    <!-- States: loading = skeleton nodes; empty = "Nothing to map yet. Connect a source or import an export." with a link to Admin, Sources; error = Alert + Retry -->
  </div>
</section>
```

### Search and ask (`#/search`)

```html
<section class="max-w-[800px]">
  <h1 class="text-[20px] font-semibold leading-7">Search and ask</h1>
  <p class="mt-1 text-muted">Keyword and meaning search across every team, limited to what you can access.</p>
  <div role="tablist" class="mt-6 flex gap-5 border-b border-rule"><!-- L2: Ask is a visible tab, not a hidden mode -->
    <button role="tab" aria-selected="true" class="h-9 border-b-2 border-ink font-medium">Search</button>
    <button role="tab" aria-selected="false" class="h-9 border-b-2 border-transparent text-muted hover:text-ink">Ask</button>
  </div>
  <form role="search" class="mt-4 flex h-10 items-center gap-2 rounded-control border border-rule bg-sheet px-3 focus-within:outline-2 focus-within:outline-accent">
    <input aria-label="Search" placeholder="Search by keyword or by meaning" class="h-full flex-1 bg-transparent placeholder:text-faint focus:outline-none">
    <!-- L3: "/" focuses this box from anywhere -->
  </form>
  <ol class="mt-4 divide-y divide-rule rounded-panel border border-rule bg-sheet">
    <li class="group relative"><a href="#/concepts/pay-runbook" class="block px-4 py-3 hover:bg-paper focus-visible:outline-offset-[-2px]">
      <span class="font-medium">Payments API incident response</span>
      <p class="mt-1 line-clamp-2 text-ink-2">Page the payments on-call first…</p></a>
      <!-- L2: "Mark as the right answer" appears on hover/focus (always visible on touch) -->
    </li>
  </ol>
  <!-- Ask's answer: font-read text-[17px] leading-7 max-w-[68ch]; citations numbered in text-accent font-semibold -->
</section>
```

### Concept (`#/concepts/<id>`, level 2)

```html
<article class="max-w-[800px]">
  <nav aria-label="Breadcrumb" class="mb-3 flex gap-1.5 text-[12px] text-muted"><a href="#/map" class="hover:text-ink">Map</a><span aria-hidden="true">/</span><span aria-current="page" class="font-mono">ledger-db</span></nav>
  <h1 class="text-[20px] font-semibold leading-7">Ledger database</h1>
  <!-- meta row: tier (dot + word), audience, updated -->
  <div class="mt-6 max-w-[68ch] font-read text-[17px] leading-7 text-ink"><!-- the one bold place: reading --></div>
  <!-- L2: History timeline, Related concepts, Sources (aside on desktop, below on phones) -->
  <!-- L3: <details class="mt-4 border-t border-rule pt-3"><summary>Raw OKF</summary>…</details> -->
</article>
```

### Admin (`#/admin/<section>`); Compliance follows the same frame

```html
<section class="max-w-[1120px]">
  <div class="mb-6 flex items-end justify-between gap-6">
    <div><h1 class="text-[20px] font-semibold leading-7">Admin</h1><p class="mt-1 text-muted">What your teams know, who's in them, and what they can ask.</p></div>
    <!-- Team picker (segmented, or a select past four teams); hidden on Review, which spans teams -->
  </div>
  <nav aria-label="Admin sections" class="-mt-2 mb-6 flex gap-1 overflow-x-auto border-b border-rule">
    <a href="#/admin/sources" aria-current="page" class="-mb-px inline-flex h-9 items-center border-b-2 border-accent px-3 font-medium text-ink">Sources</a>
    <a href="#/admin/people" class="-mb-px inline-flex h-9 items-center border-b-2 border-transparent px-3 font-medium text-muted hover:border-rule-strong hover:text-ink">People</a>
    <!-- Models · Review · Activity, same classes -->
  </nav>
  <!-- Sources: Imports first (the section's one primary action), then Connections -->
  <div class="rounded-panel border border-rule bg-sheet">
    <div class="border-b border-rule px-4 py-2"><h2 class="text-[15px] font-semibold leading-6">Imports</h2>
      <p class="text-muted">Upload a Confluence HTML space export or a Notion Markdown &amp; CSV export (a zip, up to 50 MB).</p></div>
    <form class="grid gap-3 p-4">
      <input type="file" aria-label="Export (zip)" accept=".zip"> <input aria-label="Import name" placeholder="Name, like eng-wiki" class="…input…">
      <fieldset class="flex flex-wrap gap-3"><legend class="text-muted">Readers</legend><label class="inline-flex items-center gap-2"><input type="checkbox"> group:payments</label></fieldset>
      <button type="submit" class="…primary…" disabled>Import</button>
      <!-- L2: Classification and Confluence site address sit beside the name -->
    </form>
    <!-- Rows: name, "Notion export, internal", readers (font-mono), last run (dot + "1 page, 1 changed, 0 removed"), Remove (ghost) → confirmation modal -->
  </div>
</section>
```

Compliance uses the identical frame with its own tabs: Retention (default), Legal holds, People's data, Access review, Platform.

### Connected accounts (`#/accounts`)

```html
<section class="max-w-[800px]">
  <div class="mb-6 flex items-end justify-between gap-6">
    <div><h1 class="text-[20px] font-semibold leading-7">Connected accounts</h1>
      <p class="mt-1 text-muted">Link an account by signing in to it. Documents synced from it then show up for you, still within its own permissions.</p></div>
    <div class="flex gap-2"><button class="…primary…">Link GitHub</button><!-- Link Google: secondary, only once an org admin sets up sign-in --></div>
  </div>
  <!-- Empty: "No accounts linked yet. Synced documents stay hidden until you link the accounts they're shared with." -->
</section>
```

### Command palette (overlay, ⌘K)

```html
<div class="fixed inset-0 z-50 flex items-start justify-center bg-ink/35 px-4 pt-[12vh]"><!-- scrim, not a shadow -->
  <div role="dialog" aria-modal="true" aria-label="Command palette" class="w-full max-w-[640px] overflow-hidden rounded-panel border border-rule-strong bg-sheet">
    <div class="flex h-13 items-center border-b border-rule px-4">
      <input role="combobox" aria-label="Command" aria-expanded="true" aria-controls="palette-list" aria-activedescendant="cmd-0"
             placeholder="Go to a page, run an action, or search" class="h-full flex-1 bg-transparent text-[15px] focus:outline-none">
    </div>
    <ul id="palette-list" role="listbox" aria-label="Commands" class="max-h-[56vh] overflow-y-auto p-1.5">
      <li id="cmd-0" role="option" aria-selected="true" class="flex min-h-10 items-center gap-3 rounded-control bg-tint px-2.5 py-2">
        <span class="font-medium">Admin: People</span><span class="flex-1 truncate text-muted">Members and their roles</span></li>
      <!-- Only commands this person can use. Typing two letters or more adds "Search for “…”" last. -->
    </ul>
    <div class="flex gap-4 border-t border-rule bg-paper px-4 py-2 text-[12px] text-muted">↑ ↓ to move · Enter to open · ? for shortcuts</div>
  </div>
</div>
```

### Confirmation (modal)

```html
<div class="fixed inset-0 z-50 flex items-start justify-center bg-ink/35 px-4 pt-[12vh]">
  <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-body" class="grid w-full max-w-[440px] gap-3 rounded-panel border border-rule-strong bg-sheet p-5">
    <h2 id="confirm-title" class="text-[15px] font-semibold leading-6">Remove the import “eng-wiki”?</h2>
    <p id="confirm-body" class="text-ink-2">Its pages leave search, the map, and every team, with their history. Your export file isn't affected: upload it again to bring them back.</p>
    <div class="mt-1 flex justify-end gap-2">
      <button class="…secondary…" autofocus>Cancel</button><!-- focused first: Enter never destroys by accident -->
      <button class="…destructive…">Remove import</button>
    </div>
  </div>
</div>
```

### Drawer (*specified*)

```html
<!-- Detail without losing your place: an audit event, an import run, a search result preview. -->
<aside role="dialog" aria-modal="false" aria-labelledby="drawer-title" class="fixed inset-y-0 right-0 z-40 w-full border-l border-rule bg-sheet sm:w-[480px]">
  <div class="flex h-12 items-center justify-between border-b border-rule px-4"><h2 id="drawer-title" class="font-semibold">Import run</h2>
    <button aria-label="Close" class="…ghost…">Esc</button></div>
  <div class="overflow-y-auto p-4"><!-- detail; L3: raw JSON disclosure --></div>
</aside>
```
