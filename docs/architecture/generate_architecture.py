"""The architecture diagram, as code: run it to produce aerugo-architecture.drawio beside this file, ten tabs that
explain the platform in plain terms (open it in draw.io: File, Open). Edit this script when the architecture changes,
so the diagram never goes stale. ARCHITECTURE.md beside it says the same things in words.

    python docs/architecture/generate_architecture.py
"""
import xml.etree.ElementTree as ET
from pathlib import Path

S_EXT = "rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;fontSize=12;"
S_BUILT = "rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;fontSize=12;"
S_NEW = "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=12;"
S_PEOPLE = "rounded=1;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontSize=12;"
S_SEC = "rounded=1;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;fontSize=12;"
S_DB_BUILT = "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=10;fillColor=#d5e8d4;strokeColor=#82b366;fontSize=12;"
S_DB_NEW = "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=10;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=12;"
S_GROUP = "rounded=1;whiteSpace=wrap;html=1;fillColor=none;dashed=1;strokeColor=#999999;verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;fontStyle=1;fontSize=13;"
S_BIG = "rounded=1;whiteSpace=wrap;html=1;fillColor=#eef3fb;strokeColor=#6c8ebf;strokeWidth=2;verticalAlign=top;align=center;spacingTop=6;fontStyle=1;fontSize=16;"
S_NOTE = "shape=note;whiteSpace=wrap;html=1;size=14;fillColor=#ffffff;strokeColor=#999999;align=left;spacingLeft=8;spacingRight=8;fontSize=12;verticalAlign=top;spacingTop=6;"
S_TITLE = "text;html=1;align=left;verticalAlign=top;fontSize=22;fontStyle=1;"
S_SUB = "text;html=1;align=left;verticalAlign=top;fontSize=13;whiteSpace=wrap;fontColor=#444444;"
S_STEP = "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#6c8ebf;strokeColor=none;fontColor=#ffffff;fontStyle=1;fontSize=14;"
E = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor=#555555;fontSize=11;"
E_LOOP = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeColor=#d6b656;strokeWidth=2;dashed=1;fontSize=11;fontColor=#8a6d00;"
E_STRAIGHT = "html=1;endArrow=block;endFill=1;strokeColor=#555555;fontSize=11;"

class Page:
    def __init__(self, name, title, subtitle, w=1654, h=1169):
        self.name, self.w, self.h = name, w, h
        self.cells, self.n, self.ids = [], 0, set()
        self.text(title, 40, 24, 1500, 36, S_TITLE)
        self.text(subtitle, 40, 64, 1540, 48, S_SUB)

    def _id(self, hint):
        self.n += 1
        return f"{hint}{self.n}"

    def text(self, label, x, y, w, h, style):
        return self.box(label, x, y, w, h, style)

    def box(self, label, x, y, w, h, style):
        cid = self._id("v")
        self.cells.append(("v", cid, label, style, (x, y, w, h)))
        self.ids.add(cid)
        return cid

    def edge(self, src, dst, label="", style=E, points=None):
        cid = self._id("e")
        self.cells.append(("e", cid, label, style, (src, dst, points or [])))
        return cid

    def legend(self, x=40, y=None, items=None):
        y = y if y is not None else self.h - 90
        items = items or [("Built today", S_BUILT), ("To build", S_NEW), ("The company's systems", S_EXT),
                          ("Where people meet it", S_PEOPLE), ("Security", S_SEC)]
        self.text("<b>Key</b>", x, y, 60, 24, "text;html=1;fontSize=12;")
        for i, (label, style) in enumerate(items):
            self.box(label, x + 60 + i * 175, y, 160, 30, style)

    def xml(self, diagram):
        model = ET.SubElement(diagram, "mxGraphModel", dx="1400", dy="900", grid="1", gridSize="10", guides="1",
                              tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1",
                              pageWidth=str(self.w), pageHeight=str(self.h), math="0", shadow="0")
        root = ET.SubElement(model, "root")
        ET.SubElement(root, "mxCell", id="0")
        ET.SubElement(root, "mxCell", id="1", parent="0")
        for kind, cid, label, style, geo in self.cells:
            if kind == "v":
                c = ET.SubElement(root, "mxCell", id=cid, value=label, style=style, vertex="1", parent="1")
                x, y, w, h = geo
                ET.SubElement(c, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})
            else:
                src, dst, points = geo
                assert src in self.ids and dst in self.ids, (self.name, label)
                c = ET.SubElement(root, "mxCell", id=cid, value=label, style=style, edge="1", parent="1", source=src, target=dst)
                g = ET.SubElement(c, "mxGeometry", relative="1", **{"as": "geometry"})
                if points:
                    arr = ET.SubElement(g, "Array", **{"as": "points"})
                    for px, py in points:
                        ET.SubElement(arr, "mxPoint", x=str(px), y=str(py))

pages = []

# ---------------------------------------------------------------- 1. the big picture
p = Page("1. The big picture", "Aerugo: the company's living memory",
         "It reads what the company already has, understands how it fits together, and answers anyone in their own "
         "context: with evidence, and only what they're allowed to see. People correct it, and it learns. It's a "
         "senior colleague's memory and legwork, available to everyone, at any hour.")
p.box("<b>Where knowledge lives today</b>", 40, 130, 300, 30, "text;html=1;fontSize=14;align=center;")
srcs = ["<b>Documents</b><br>Confluence, SharePoint, Drive", "<b>Tickets</b><br>Jira, ServiceNow",
        "<b>Conversations</b><br>Teams, Slack, meetings", "<b>Code</b><br>GitHub repositories",
        "<b>Data</b><br>EDW, BigQuery, catalogs", "<b>Jobs</b><br>Control-M, Airflow",
        "<b>People</b><br>the directory (Entra, Okta)", "<b>Operations</b><br>monitoring, incidents"]
left = [p.box(t, 60, 170 + i * 82, 260, 64, S_EXT) for i, t in enumerate(srcs)]
big = p.box("Aerugo", 470, 170, 640, 640, S_BIG)
core = p.box("<b>Knowledge core</b><br>what the company knows: facts, procedures, policies, contacts, definitions, "
             "decisions, lessons. Each with its evidence, owner, who it applies to, and how fresh it is.", 500, 220, 580, 110, S_NEW)
atlas = p.box("<b>Atlas</b><br>how everything connects: systems, code, jobs, tables, who uses what, what feeds what.",
              500, 350, 580, 90, S_NEW)
agent = p.box("<b>Architect agent and playbooks</b><br>thinks in steps, like a senior: traces a flow, plans a migration, "
              "walks a procedure, finds the right person.", 500, 460, 580, 90, S_NEW)
answers = p.box("<b>Answers and guidance</b><br>search and Ask today; incident mode, onboarding paths, migration plans, "
                "proposed pull requests next.", 500, 570, 580, 90, S_BUILT)
guard = p.box("<b>On everything:</b> permissions from each source, evidence for every claim, an audit trail, "
              "no monitoring of people", 500, 690, 580, 90, S_SEC)
p.box("<b>Where people meet it</b>", 1250, 130, 340, 30, "text;html=1;fontSize=14;align=center;")
outs = ["<b>The web app</b><br>ask, explore, review", "<b>Teams and Slack</b><br>ask and save where they already talk",
        "<b>Phones and handhelds</b><br>stores and distribution centers", "<b>IDE and pull requests</b><br>impact while coding",
        "<b>APIs and agents (MCP)</b><br>other tools build on it"]
right = [p.box(t, 1270, 190 + i * 110, 300, 76, S_PEOPLE) for i, t in enumerate(outs)]
for c in left:
    p.edge(c, big)
for c in right:
    p.edge(big, c)
p.edge(right[1], big, "people confirm, correct, ask: it learns", E_LOOP, points=[(1420, 900), (790, 900)])
p.legend()
pages.append(p)

# ---------------------------------------------------------------- 2. the components
p = Page("2. Components", "Inside Aerugo: the parts, layer by layer",
         "Read it bottom to top: connectors bring knowledge in, pipelines turn it into facts and knowledge, stores keep "
         "it with its permissions, services reason over it, and surfaces put it in front of people. The column on the "
         "right runs through every layer.")
layers = [
    ("Surfaces: where people meet it", 130, [("Web app (Next.js)", S_BUILT), ("Teams and Slack bots", S_NEW),
                                              ("Frontline mobile view", S_NEW), ("IDE extension, pull-request bot", S_NEW),
                                              ("MCP server, APIs, SDKs", S_BUILT)]),
    ("Services: what it does", 290, [("Answers (search, Ask)", S_BUILT), ("Architect agent, playbooks", S_NEW),
                                       ("Incident mode, onboarding paths", S_NEW), ("Migrations", S_NEW),
                                       ("Change agent (pull requests)", S_NEW), ("Review queue, gaps", S_BUILT)]),
    ("Pipelines: how it understands (the worker)", 450, [("Indexing: chunks, embeddings", S_BUILT),
                                                          ("Extraction: knowledge units", S_BUILT),
                                                          ("Consolidation: merge, supersede, conflicts", S_BUILT),
                                                          ("Analyzers: code, SQL lineage, usage, jobs", S_NEW),
                                                          ("Profiles and systems", S_NEW),
                                                          ("Lifecycle: review, staleness, retirement", S_BUILT),
                                                          ("Learning loop: feedback, gaps, experts", S_NEW)]),
    ("Stores: what it keeps", 610, [("Documents and index (Postgres, pgvector)", S_DB_BUILT),
                                     ("Knowledge units", S_DB_BUILT), ("Atlas graph", S_DB_BUILT), ("Questions and gaps", S_DB_BUILT),
                                     ("Usage", S_DB_NEW),
                                     ("Permissions (OpenFGA)", S_DB_BUILT), ("Credentials (OpenBao, Secret Manager)", S_DB_BUILT)]),
    ("Connectors and capture: how it reads", 770, [("Docs: Confluence, Drive, SharePoint, Notion", S_BUILT),
                                                    ("Tickets and chat: Jira, Slack", S_BUILT),
                                                    ("Teams capture, meetings, handovers", S_NEW),
                                                    ("Code: GitHub (thousands of repos)", S_BUILT),
                                                    ("Warehouses, catalogs, usage logs", S_NEW),
                                                    ("Schedulers and ETL: Control-M, Airflow", S_NEW)]),
]
rows = []
for title, y, items in layers:
    p.box(title, 40, y, 1200, 140, S_GROUP)
    w = (1200 - 30 - (len(items) - 1) * 14) / len(items)
    rows.append([p.box(lbl, 55 + i * (w + 14), y + 40, w, 80, st) for i, (lbl, st) in enumerate(items)])
for lower, upper in zip(rows[::-1][:-1], rows[::-1][1:]):
    p.edge(lower[0], upper[0])
p.box("<b>Across every layer</b>", 1280, 130, 330, 780, S_GROUP)
cross = [("Identity: sign-on, and the asker's role and place", S_BUILT), ("Permissions: row-level security and OpenFGA", S_BUILT),
         ("Model gateway: approved models, budgets", S_BUILT), ("Audit and privacy", S_BUILT),
         ("Job queue with leases", S_BUILT), ("Observability: telemetry, alerts", S_BUILT),
         ("Sensitive-topic filters", S_NEW), ("Question sets: tests for answers", S_NEW)]
for i, (lbl, st) in enumerate(cross):
    p.box(lbl, 1300, 175 + i * 88, 290, 64, st)
p.legend()
pages.append(p)

# ---------------------------------------------------------------- 3. data residency
p = Page("3. What's stored", "Data residency: does Aerugo keep a copy?",
         "The sources stay the system of record: people keep writing in Confluence, Jira, and the rest. Aerugo can keep "
         "a full searchable copy, only pointers plus small derived facts, or nothing but pointers, chosen source by source. "
         "Everything it keeps stays inside the company's own cloud and region.")
modes = [("<b>A. Index</b> (today)", 60, [True, True, True, True, True],
          "<b>Best answers, fastest.</b> Works when a source is down. A protected copy of text exists: encrypted, "
          "permission-checked, deleted when the source deletes it."),
         ("<b>B. Pointer</b> (recommended when copies aren't allowed)", 560, [True, True, True, True, False],
          "<b>No second copy of content.</b> Text is fetched from the source at answer time and thrown away after. "
          "Slower, and depends on the source being up. Embeddings still reveal meaning: protect them."),
         ("<b>C. Live only</b>", 1060, [True, True, False, False, False],
          "<b>Almost nothing stored.</b> Uses each source's own search when someone asks. Weakest answers, slowest, and "
          "no map of how things connect.")]
kept = ["Pointer and metadata (title, address, owner)", "Who may read it (permissions)",
        "Small derived facts with pointers (the map, knowledge units)", "Embeddings (numbers that capture meaning)",
        "Text passages (a copy)"]
for title, x, flags, note in modes:
    p.box(title, x, 130, 470, 470, S_GROUP)
    for i, (k, on) in enumerate(zip(kept, flags)):
        style = (S_BUILT if on else "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#cccccc;fontColor=#aaaaaa;dashed=1;fontSize=12;")
        p.box(("&#10003; " if on else "&#10007; ") + k, x + 20, 175 + i * 62, 430, 48, style)
    p.box(note, x + 20, 490, 430, 96, S_NOTE)
p.box("<b>How B works when someone asks</b>", 60, 630, 600, 30, "text;html=1;fontSize=14;")
flow = ["Person asks", "Find candidates: pointers, embeddings, facts", "Check their permission in the source",
        "Fetch only the passages needed, live", "Answer with citations", "Discard the text: nothing kept"]
fb = [p.box(t, 60 + i * 255, 680, 225, 70, S_PEOPLE if i in (0, 4) else (S_SEC if i == 2 else S_NEW)) for i, t in enumerate(flow)]
for a, b in zip(fb, fb[1:]):
    p.edge(a, b)
p.box("<b>What can't be pointer-only:</b> the map. \"Who uses this table\", \"what feeds this report\", \"which repos "
      "change together\" are worked out ahead of time, across thousands of sources, and kept as small facts with "
      "pointers back to their evidence. They're derived knowledge, not copies of documents, and they carry the same "
      "permissions as their sources.", 60, 790, 1490, 90, S_NOTE)
p.legend(items=[("Kept", S_BUILT), ("Not kept", "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#cccccc;fontColor=#aaaaaa;dashed=1;fontSize=12;"),
                ("People", S_PEOPLE), ("Security", S_SEC), ("Aerugo", S_NEW)])
pages.append(p)

# ---------------------------------------------------------------- 4. answering
p = Page("4. Answering a question", "How a question gets answered",
         "Nine steps, in about the time it takes to read this. Every answer says where it came from and how sure it "
         "is, and it never uses anything the person isn't allowed to see.")
steps = [("Ask", "in Teams, the web app, a phone, or the IDE", S_PEOPLE),
         ("Who's asking", "sign-in, their team, role, and location", S_SEC),
         ("Find", "knowledge units first, then documents and the Atlas", S_BUILT),
         ("Filter", "only what this person may see, in the database itself", S_SEC),
         ("Fetch", "in pointer mode, the passages needed, live from the source", S_NEW),
         ("Think", "a playbook's steps, with the company's approved model", S_NEW),
         ("Check", "every claim against its evidence; drop what isn't supported", S_SEC),
         ("Answer", "citations, confidence, and both sides when teams disagree", S_PEOPLE),
         ("Learn", "\"this is wrong\", or \"ask an expert\": it gets better", S_PEOPLE)]
xs = [60, 560, 1060]
cells = []
for i, (t, d, st) in enumerate(steps):
    x, y = xs[i % 3], 150 + (i // 3) * 230
    p.box(str(i + 1), x, y + 30, 50, 50, S_STEP)
    cells.append(p.box(f"<b>{t}</b><br>{d}", x + 70, y, 380, 110, st))
for a, b in zip(cells, cells[1:]):
    p.edge(a, b)
p.box("<b>Example.</b> A store associate asks: \"A customer slipped near the entrance. What do I do?\" "
      "It answers with that store's incident procedure as a checklist, who to call by role (the manager on duty, the "
      "district manager, loss prevention), the form to fill in, and what not to do, citing the procedure's page and "
      "its owner.", 60, 860, 1490, 90, S_NOTE)
p.legend()
pages.append(p)

# ---------------------------------------------------------------- 5. the loop
p = Page("5. How it learns", "The self-evolving loop",
         "It doesn't rewrite itself. Its knowledge, confirmations, and playbooks grow, and every change to how it answers "
         "is tested against real questions before it ships.")
ring = [("Capture", "documents, opted-in chats, meetings, handovers", 660, 140),
        ("Propose", "draft knowledge, with its evidence", 1080, 300),
        ("Confirm or correct", "the owner decides; corrections are kept", 1080, 560),
        ("Consolidate", "merge duplicates, supersede old, flag conflicts", 660, 720),
        ("Answer", "people use it, in their own context", 240, 560),
        ("Notice gaps", "unanswered questions, stale or conflicting knowledge", 240, 300)]
rc = [p.box(f"<b>{t}</b><br>{d}", x, y, 300, 90, S_NEW if i not in (2, 4) else S_PEOPLE) for i, (t, d, x, y) in enumerate(ring)]
for a, b in zip(rc, rc[1:] + rc[:1]):
    p.edge(a, b)
p.box("<b>Tests gate every change</b><br>per-team question sets, with right answers", 640, 420, 340, 90, S_SEC)
p.box("<b>The synthetic retailer</b><br>a company and its data estate with known right answers, loaded through the connectors", 640, 530, 340, 90, S_BUILT)
p.box("<b>Ask an expert</b>: a gap goes to whoever knows; with their consent, their answer becomes knowledge for "
      "the next person.", 60, 860, 720, 70, S_NOTE)
p.box("<b>Freshness</b>: every piece of knowledge has a review schedule; stale pieces go back to their owners.",
      830, 860, 720, 70, S_NOTE)
p.legend()
pages.append(p)

# ---------------------------------------------------------------- 6. capture
p = Page("6. Capture", "Capturing what isn't written down",
         "Much of what seniors know lives in conversations. It's captured only where people choose, filtered for "
         "privacy, and confirmed by an owner before anyone relies on it.")
caps = ["<b>Teams channel</b>, opted in by its owners", "<b>\"Save to Aerugo\"</b> on any message",
        "<b>Slack channel</b>, opted in", "<b>Meeting transcripts</b>, opted in by the organizer",
        "<b>Handover interview</b> when someone leaves", "<b>Ask an expert</b> when an answer is missing"]
cc = [p.box(t, 60, 150 + i * 100, 320, 70, S_PEOPLE) for i, t in enumerate(caps)]
filt = p.box("<b>Privacy filters</b><br>only opted-in places<br>\"don't capture\" marks honored<br>direct messages never, by default<br>"
             "HR, medical, and legal matters excluded<br>personal data excluded", 520, 300, 320, 220, S_SEC)
prop = p.box("<b>Proposed knowledge</b><br>a procedure, an answer, a decision, a contact, with the messages it came from",
             940, 330, 300, 140, S_NEW)
own = p.box("<b>The owner confirms or corrects</b>", 1300, 300, 270, 90, S_PEOPLE)
core = p.box("<b>Knowledge core</b>", 1300, 450, 270, 90, S_NEW)
for c in cc:
    p.edge(c, filt)
p.edge(filt, prop)
p.edge(prop, own)
p.edge(own, core)
p.box("<b>Teams' rules:</b> a channel's owners can consent to a bot reading all its messages. Group-chat delivery to "
      "bots is unreliable, and live notifications aren't available for private and shared channels, so capture rests "
      "on opted-in standard channels and the save action, never on watching chats.", 60, 800, 1490, 80, S_NOTE)
p.legend()
pages.append(p)

# ---------------------------------------------------------------- 7. engineering and data
p = Page("7. Engineering and data", "The engineering and data pack: lineage, usage, migrations",
         "One pack among many. It maps how data and code actually move, ranks what matters by real usage, and plans "
         "migrations like a senior would: for example, an EDW cube to the cloud platform (EDP on GCP).")
src7 = ["<b>GitHub</b><br>repos, Terraform, conventions", "<b>EDW</b><br>metadata, query logs", "<b>Control-M</b><br>jobs, order, scripts",
        "<b>Airflow, Composer</b><br>DAGs", "<b>dbt, Dataform</b><br>models", "<b>Catalogs</b><br>Knowledge Catalog, Dataplex"]
s7 = [p.box(t, 60, 140 + i * 105, 260, 75, S_EXT) for i, t in enumerate(src7)]
an = p.box("<b>Analyzers</b> (facts, no AI)<br>repo facts, SQL lineage,<br>usage, the job graph,<br>naming and Terraform conventions",
           400, 300, 260, 160, S_NEW)
at = p.box("<b>Atlas</b><br>tables, jobs, repos, owners, usage, and every link, with evidence", 740, 320, 260, 120, S_NEW)
ag = p.box("<b>Architect agent</b><br>migration playbook", 1080, 320, 220, 120, S_NEW)
outs7 = ["<b>Migration assessment</b><br>waves by dependency, ranked by usage; retire what's unused",
         "<b>Lineage diagrams</b>", "<b>Step-by-step plan</b><br>schema, Terraform, catalog metadata, validation",
         "<b>Pull requests</b><br>reviewed and merged by people"]
o7 = [p.box(t, 1360, 160 + i * 120, 240, 90, S_PEOPLE) for i, t in enumerate(outs7)]
for c in s7:
    p.edge(c, an)
p.edge(an, at)
p.edge(at, ag)
for c in o7:
    p.edge(ag, c)
p.box("<b>The question it answers:</b> \"This EDW cube needs to move to EDP. How do we do it?\" What feeds it, what it "
      "feeds, who uses it and how often, how our repos and Terraform name and define things, how its metadata maps to "
      "the catalog, in what order to move it, how to prove the copy is right, and draft pull requests for each step.",
      60, 800, 1490, 90, S_NOTE)
p.legend()
pages.append(p)

# ---------------------------------------------------------------- 8. security
p = Page("8. Security and trust", "Security and trust: what keeps it safe",
         "The same permissions as the sources, enforced in the database. Read-only toward every source. Every action is "
         "a person's decision. Nothing is used to watch people.")
person = p.box("<b>A person</b>", 60, 380, 180, 80, S_PEOPLE)
sso = p.box("<b>Sign-in</b><br>the company's identity provider; during a pilot, only onboarded teams", 300, 370, 220, 100, S_SEC)
api = p.box("<b>Aerugo</b>", 580, 380, 200, 80, S_NEW)
fga = p.box("<b>Permissions</b><br>mirrored from each source; checked in the database (row-level security) and OpenFGA",
            860, 220, 320, 100, S_SEC)
cred = p.box("<b>Credentials</b><br>read-only service accounts; secrets only as references (OpenBao, Secret Manager)",
             860, 340, 320, 100, S_SEC)
models = p.box("<b>Models</b><br>only the company's approved ones, through the gateway, with budgets", 860, 460, 320, 100, S_SEC)
audit = p.box("<b>Audit</b><br>every read and action recorded", 860, 580, 320, 80, S_SEC)
src8 = p.box("<b>The sources</b><br>read-only; written only by reviewed pull requests, in repos that opted in",
             1260, 340, 320, 100, S_EXT)
p.edge(person, sso)
p.edge(sso, api)
for c in (fga, cred, models, audit):
    p.edge(api, c)
p.edge(cred, src8)
p.box("<b>Never:</b> ranking people by their questions or activity; capturing a channel nobody opted in; answering "
      "from something the asker can't see; merging a change by itself.", 60, 760, 1490, 70, S_NOTE)
p.legend()
pages.append(p)

# ---------------------------------------------------------------- 9. deployment
p = Page("9. Where it runs", "Where it runs: inside the company's own cloud",
         "Self-hosted in the company's own project and region. Nothing goes to an outside vendor. Sources in the "
         "company's data centers are reached through a runner inside their network that only calls out.")
proj = p.box("<b>The company's GCP project</b> (region pinned)", 380, 140, 860, 560, S_GROUP)
gke = p.box("<b>Kubernetes (GKE)</b><br>web app, API, worker, MCP server, permissions", 420, 200, 380, 120, S_NEW)
sql = p.box("<b>Cloud SQL</b><br>Postgres with pgvector", 860, 200, 340, 120, S_DB_NEW)
sm = p.box("<b>Secret Manager</b><br>references only", 420, 360, 380, 100, S_SEC)
vx = p.box("<b>Vertex AI</b><br>models, in the same region", 860, 360, 340, 100, S_NEW)
logs = p.box("<b>Logging and monitoring</b>", 420, 500, 380, 80, S_BUILT)
idp = p.box("<b>Identity provider</b><br>Entra or Okta", 860, 500, 340, 80, S_SEC)
cloud = p.box("<b>Cloud sources</b><br>Confluence, GitHub, Teams, Slack", 1300, 260, 300, 100, S_EXT)
onprem = p.box("<b>The company's data centers</b><br>EDW, Control-M", 40, 300, 300, 100, S_EXT)
runner = p.box("<b>Runner</b><br>inside their network, calls out only", 40, 460, 300, 90, S_NEW)
users = p.box("<b>Employees</b>", 1300, 520, 300, 80, S_PEOPLE)
p.edge(gke, sql)
p.edge(gke, vx)
p.edge(gke, sm)
p.edge(gke, cloud, "read-only, allowlisted")
p.edge(onprem, runner, "")
p.edge(runner, gke, "outbound only")
p.edge(users, gke, "single sign-on")
p.box("<b>Data residency, answered:</b> everything stays in the company's project and region; with pointer mode, "
      "not even a copy of documents' text is kept (tab 3).", 60, 760, 1490, 70, S_NOTE)
p.legend()
pages.append(p)

# ---------------------------------------------------------------- 10. the build
p = Page("10. How we build it", "How we build it: foundations first",
         "Each phase is proven on a synthetic company with known right answers before anything touches real data, "
         "and reviewed before the next begins.")
phases = [("Phase 0", "Production fixes", "make what exists solid at scale", S_SEC),
          ("Foundation", "Knowledge core, learning loop, question sets, the synthetic company, the Atlas", "everything else stands on it", S_NEW),
          ("Capture", "Teams, Slack, meetings, handovers", "what isn't written down", S_NEW),
          ("Surfaces", "Screens, Teams and Slack bots, the frontline view", "where people are", S_NEW),
          ("Packs", "Onboarding, incidents, policies, contact center, merchandising, supply chain, recalls, engineering and migration, leaders", "the scenarios, team by team", S_NEW)]
pc = []
for i, (t, d, why, st) in enumerate(phases):
    x = 60 + i * 310
    pc.append(p.box(f"<b>{t}</b><br>{d}", x, 180, 280, 160, st))
    p.box(f"<i>{why}</i>", x, 360, 280, 50, "text;html=1;align=center;fontSize=12;fontColor=#555555;whiteSpace=wrap;")
for a, b in zip(pc, pc[1:]):
    p.edge(a, b)
p.box("<b>Today</b>: documents, permissions, search and Ask, connectors for Confluence, Jira, Drive, GitHub, Slack, "
      "Microsoft 365, Notion, catalogs, and directories, each syncing only what changed and surviving rate limits; "
      "the Map; the Terraform provider; Kubernetes manifests.",
      60, 470, 1490, 70, S_BUILT)
p.box("<b>Why foundations first:</b> a connector only adds raw material. What makes it a senior's memory is the core "
      "that turns material into knowledge, keeps it fresh, and learns from people. Built once, every new connector and "
      "every new team's pack makes it smarter.", 60, 580, 1490, 80, S_NOTE)
p.legend()
pages.append(p)

mxfile = ET.Element("mxfile", host="app.diagrams.net", agent="Aerugo architecture generator", version="24.0.0")
for i, pg in enumerate(pages):
    d = ET.SubElement(mxfile, "diagram", id=f"page{i + 1}", name=pg.name)
    pg.xml(d)
out = Path(__file__).with_name("aerugo-architecture.drawio")
ET.ElementTree(mxfile).write(out, encoding="utf-8", xml_declaration=False)
print(f"{out}: {len(pages)} pages, {sum(len(pg.cells) for pg in pages)} shapes and arrows")
