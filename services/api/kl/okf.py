"""Open Knowledge Format v0.2 (M3, slice C1): parse, render, validate, and bronze conversion.

We follow OKF as published. Our governance lives only under the `x-kl` key. As the spec requires,
unknown keys are preserved on round-trip and never rejected.

    python -m kl.okf check <bundle-dir> [--knowledge] [--audience-floor <audience>]
"""
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

STATUSES = {"draft", "stable", "deprecated"}
CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
RELATION_TYPES = {"owns", "owned-by", "depends-on", "approves", "derived-from", "supersedes"}
RESERVED_FILES = {"index.md", "log.md"}
# Audiences from widest to narrowest. "source" means exactly the source document's readers.
AUDIENCE_RANK = {"org": 0, "workspace": 1, "group": 2, "source": 3}

_ACTOR = re.compile(r"^(human|process):[A-Za-z0-9._@-]+$|^[A-Za-z0-9._-]+/[A-Za-z0-9._+-]+$")
_AUDIENCE = re.compile(r"^(org|source|workspace/[a-z0-9][a-z0-9-]{0,39}|group/[A-Za-z0-9._-]{1,64})$")
_LINK = re.compile(r"\]\((/[^)\s#]+)(?:#[^)]*)?\)")
_FOOTNOTE = re.compile(r"\[\^([A-Za-z0-9._-]+)\](?!:)")
CONCEPT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Knowledge CI refuses these. Findings name the kind and line, never the value.
_SENSITIVE = [
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("US Social Security number", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
]
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


@dataclass
class Concept:
    path: str   # bundle-relative, e.g. /systems/ledger-db.md
    meta: dict  # frontmatter, unknown keys included
    body: str


def parse(text: str, path: str) -> Concept:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return Concept(path, {}, text)
    end = text.find("\n---\n", 3)
    if end == -1:
        if not text.rstrip().endswith("\n---"):
            raise ValueError("frontmatter is not closed")
        end = text.rstrip().rfind("\n---")
    meta = yaml.safe_load(text[4:end]) or {}
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a mapping")
    return Concept(path, meta, text[end + 5:].lstrip("\n"))


def render(concept: Concept) -> str:
    front = yaml.safe_dump(concept.meta, sort_keys=False, allow_unicode=True)
    return f"---\n{front}---\n\n{concept.body.strip()}\n"


def _is_time(value) -> bool:
    if isinstance(value, (datetime, date)):
        return True
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
    return False


def audience_wider(audience: str, floor: str) -> bool:
    """True when audience reaches people that floor (the narrowest source audience) doesn't."""
    kind, floor_kind = audience.split("/")[0], floor.split("/")[0]
    if AUDIENCE_RANK[kind] != AUDIENCE_RANK[floor_kind]:
        return AUDIENCE_RANK[kind] < AUDIENCE_RANK[floor_kind]
    return audience != floor  # same kind, different scope: not contained, so it counts as wider


def validate(concept: Concept, known_paths: set[str] | None = None, audience_floor: str | None = None) -> list[str]:
    """Problems with one concept (empty means valid). known_paths enables link and relation checks."""
    meta, problems = concept.meta, []

    def bad(message: str) -> None:
        problems.append(f"{concept.path}: {message}")

    if not meta:
        bad("missing frontmatter")
        return problems
    if not isinstance(meta.get("type"), str) or not meta["type"].strip():
        bad("`type` is required")
    if "status" in meta and meta["status"] not in STATUSES:
        bad(f"`status` must be one of {', '.join(sorted(STATUSES))}")
    if "stale_after" in meta and not _is_time(meta["stale_after"]):
        bad("`stale_after` must be an ISO 8601 timestamp")
    generated = meta.get("generated")
    if generated is not None and not (isinstance(generated, dict) and _ACTOR.match(str(generated.get("by", "")))
                                      and _is_time(generated.get("at"))):
        bad("`generated` needs an actor `by` and an ISO 8601 `at`")
    verified = meta.get("verified")
    for entry in [verified] if isinstance(verified, dict) else (verified or []):
        if not (isinstance(entry, dict) and _ACTOR.match(str(entry.get("by", ""))) and _is_time(entry.get("at"))):
            bad("each `verified` entry needs an actor `by` and an ISO 8601 `at`")

    source_ids: list[str] = []
    sources = meta.get("sources") or []
    if not isinstance(sources, list):
        bad("`sources` must be a list")
    else:
        for source in sources:
            if isinstance(source, dict) and source.get("id") and source.get("resource"):
                source_ids.append(str(source["id"]))
            else:
                bad("each source needs an `id` and a `resource`")
        if len(source_ids) != len(set(source_ids)):
            bad("source ids must be unique")
    if source_ids:  # per-claim attribution: footnotes are keyed to source ids
        for ref in _FOOTNOTE.findall(concept.body):
            if ref not in source_ids:
                bad(f"footnote [^{ref}] doesn't match a source id")

    xkl = meta.get("x-kl")
    if xkl is not None and not isinstance(xkl, dict):
        bad("`x-kl` must be a mapping")
    elif xkl:
        if "classification" in xkl and xkl["classification"] not in CLASSIFICATIONS:
            bad(f"`x-kl.classification` must be one of {', '.join(sorted(CLASSIFICATIONS))}")
        audience = xkl.get("audience")
        if audience is not None and not _AUDIENCE.match(str(audience)):
            bad("`x-kl.audience` must be org, source, workspace/<slug>, or group/<id>")
        elif audience and audience_floor and audience_wider(str(audience), audience_floor):
            bad(f"audience {audience} is wider than its sources allow ({audience_floor})")
        owners = xkl.get("owners")
        if owners is not None and not (isinstance(owners, list) and all(isinstance(o, str) for o in owners)):
            bad("`x-kl.owners` must be a list of strings")
        for relation in xkl.get("relations") or []:
            target = str(relation.get("target", "")) if isinstance(relation, dict) else ""
            # A target is a path in this bundle, or kl:<id> for a concept in another bundle.
            in_bundle = target.startswith("/")
            if not isinstance(relation, dict) or relation.get("type") not in RELATION_TYPES or not (
                    in_bundle or (target.startswith("kl:") and CONCEPT_KEY.fullmatch(target[3:]))):
                bad("each relation needs a known `type` and a `target` that's a bundle path or kl:<id>")
            elif in_bundle and known_paths is not None and target not in known_paths:
                bad(f"relation target {target} doesn't exist in the bundle")
        aliases = xkl.get("aliases")
        if aliases is not None and not (isinstance(aliases, list) and all(isinstance(a, str) and a.strip() for a in aliases)):
            bad("`x-kl.aliases` must be a list of non-empty strings")

    if known_paths is not None:
        for link in _LINK.findall(concept.body):
            if link not in known_paths:
                bad(f"broken link {link}")
    return problems


def _luhn(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def scan_sensitive(text: str, path: str) -> list[str]:
    """Secrets and PII that must not go into a knowledge bundle."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        kinds = [kind for kind, pattern in _SENSITIVE if pattern.search(line)]
        if any(_luhn(re.sub(r"\D", "", m.group())) for m in _CARD.finditer(line)):
            kinds.append("payment card number")
        found += [f"{path}:{number}: possible {kind}; remove it before merging" for kind in kinds]
    return found


def check_files(files: dict[str, str], audience_floor: str | None = None,
                knowledge: bool = False) -> tuple[list[str], dict[str, Concept]]:
    """Validate a bundle given as {bundle path: text}. Returns (problems, parsed concepts). In a
    knowledge bundle every concept needs a unique `x-kl.id`, and secrets or PII fail the check."""
    known, problems, parsed, ids = set(files), [], {}, {}
    for path, text in sorted(files.items()):
        if knowledge:
            problems += scan_sensitive(text, path)
        if path.rsplit("/", 1)[-1] in RESERVED_FILES:
            continue
        try:
            concept = parse(text, path)
        except (ValueError, yaml.YAMLError) as e:
            problems.append(f"{path}: {e}")
            continue
        problems += validate(concept, known, audience_floor)
        if knowledge:
            key = (concept.meta.get("x-kl") or {}).get("id") if isinstance(concept.meta.get("x-kl"), dict) else None
            if not isinstance(key, str) or not CONCEPT_KEY.fullmatch(key):
                problems.append(f"{path}: `x-kl.id` is required in a knowledge bundle (letters, digits, . _ -)")
            elif key in ids:
                problems.append(f"{path}: `x-kl.id` {key} is already used by {ids[key]}")
            else:
                ids[key] = path
        parsed[path] = concept
    return problems, parsed


def check_bundle(root: str, audience_floor: str | None = None, knowledge: bool = False) -> list[str]:
    base = Path(root)
    files = {"/" + p.relative_to(base).as_posix(): p.read_text(encoding="utf-8")
             for p in sorted(base.rglob("*.md")) if ".git" not in p.parts}
    return check_files(files, audience_floor, knowledge)[0]


def bronze(*, key: str, title: str, resource: str, source_version: str | None, classification: str,
           sections: list[tuple[str | None, str]], generated_at: str) -> str:
    """A bronze concept: one source document as OKF, pinned to the revision it came from."""
    parts = [f"## {heading.split(' > ')[-1]}\n\n{text}" if heading else text for heading, text in sections]
    meta = {
        "type": "Document",
        "title": title,
        "resource": resource,
        "status": "stable",
        "generated": {"by": "kl-bronze/0.1", "at": generated_at},
        "sources": [{"id": "source", "resource": resource}],
        "x-kl": {"id": key, "classification": classification, "audience": "source",
                 "derivation": {"from": key, "source_version": source_version}},
    }
    return render(Concept(f"/bronze/{key}.md", meta, f"# {title}\n\n" + "\n\n".join(parts)))


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "check":
        print("usage: python -m kl.okf check <bundle-dir> [--knowledge] [--audience-floor <audience>]")
        return 2
    floor = argv[argv.index("--audience-floor") + 1] if "--audience-floor" in argv else None
    problems = check_bundle(argv[1], floor, knowledge="--knowledge" in argv)
    for problem in problems:
        print(problem)
    print(f"{'FAIL' if problems else 'OK'}: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
