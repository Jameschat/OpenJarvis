"""vault_graph.py — in-process markdown → graph.json extractor.

Path B of the graphify-broken-deferred resolution (2026-04-29). Earlier
graphify versions ingested markdown vaults; the current CLI is code-only.
This module replaces the external graphify dependency for the vault
use case with a small, owned, structural extractor.

Output schema matches what graphify_bridge.py + the HUD's standalone
/graphify viz already consume:

    {
      "nodes": [{"id", "label", "source_file", "community", "tags"}],
      "links": [{"source", "target", "relation", "confidence"}]
    }

What it captures:
    - One node per .md file in the vault
    - Edges from [[wikilinks]] in the body (relation: "wikilink")
    - Edges from frontmatter `parent: [[X]]` (relation: "parent")
    - Edges from frontmatter `related: [[X]]` lists (relation: "related")
    - Community = folder bucket (Decisions=0, Knowledge=1, etc.)
    - Tags pulled from frontmatter for downstream filtering

What it does NOT capture (deferred — out of scope for Path B v1):
    - LLM-driven semantic clustering / community labelling — folder-
      based grouping is structural-only.
    - Embedding-based similarity edges — only explicit wikilinks count.
    - Tag-overlap edges (could add later: same-tag = low-confidence edge).

This module is pure Python stdlib — no PyYAML dependency. The
frontmatter parser handles the simple YAML shapes Jarvis vault files
use (key: value, key: [list, of, values], key: "string"). Anything
fancier is ignored gracefully.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Folder → community number. Stable across rebuilds so the HUD's palette
# colour-by-community stays visually consistent day to day.
# ---------------------------------------------------------------------------

_COMMUNITY_BY_FOLDER: Dict[str, int] = {
    "Decisions": 0,
    "Knowledge": 1,
    "Sessions": 2,
    "Daily": 3,
    "Projects": 4,
    "People": 5,
    "Scheduled": 6,
    "Content": 7,
    "ChatGPT": 8,
    "Inbox": 9,
    # vault root (00 Index, 00 Session Handoff, etc.) gets 10
    "_root": 10,
}


def _community_for_path(rel_path: str) -> int:
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return _COMMUNITY_BY_FOLDER["_root"]
    return _COMMUNITY_BY_FOLDER.get(parts[0], 11)   # 11 = "other"


# ---------------------------------------------------------------------------
# Node ID — stable slug derived from relative path, lowercased, non-alnum
# replaced with underscores. Same file always gets the same id across rebuilds.
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _node_id(rel_path: str) -> str:
    p = rel_path.replace("\\", "/")
    if p.endswith(".md"):
        p = p[:-3]
    s = _SLUG_RE.sub("_", p.lower()).strip("_")
    return s or "untitled"


# ---------------------------------------------------------------------------
# Frontmatter — minimal stdlib YAML-ish parser
# ---------------------------------------------------------------------------

_FM_BLOCK_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Return (frontmatter_dict, body). frontmatter_dict is empty if
    none / unparseable. Handles `key: value`, `key: [a, b]`,
    `key: "quoted string"`, and multi-line list:
        tags:
          - foo
          - bar
    Anything more exotic is ignored gracefully."""
    m = _FM_BLOCK_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    fm: Dict[str, Any] = {}

    lines = raw.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            i += 1
            continue
        # Multi-line list: bare key, then indented "- item" lines
        if not val:
            items: List[str] = []
            j = i + 1
            while j < len(lines):
                child = lines[j]
                cstripped = child.strip()
                if not cstripped:
                    j += 1
                    continue
                if not (child.startswith(" ") or child.startswith("\t")):
                    break
                if cstripped.startswith("- "):
                    items.append(cstripped[2:].strip().strip('"').strip("'"))
                else:
                    break
                j += 1
            if items:
                fm[key] = items
            i = j
            continue
        # Inline list: [a, b, c]
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            fm[key] = [
                p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()
            ]
            i += 1
            continue
        # Quoted string
        if (val.startswith('"') and val.endswith('"')) or \
           (val.startswith("'") and val.endswith("'")):
            fm[key] = val[1:-1]
            i += 1
            continue
        # Plain scalar
        fm[key] = val
        i += 1
    return fm, body


# ---------------------------------------------------------------------------
# Wikilink extraction — handles [[note]] and [[note|alias]]
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


def _extract_wikilinks(text: str) -> List[str]:
    out: List[str] = []
    for m in _WIKILINK_RE.finditer(text or ""):
        target = m.group(1).split("|")[0].strip()
        if target:
            out.append(target)
    # Preserve order, dedupe
    seen: set = set()
    uniq = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


# ---------------------------------------------------------------------------
# Vault walk
# ---------------------------------------------------------------------------

# Directories under the vault we DON'T traverse (templates / attachments / etc.
# Operator's vault doesn't currently have these but documenting for future).
_SKIP_DIRS = {".obsidian", ".trash", "Templates", "_attachments", ".graphify"}


def _walk_vault(root: Path) -> List[Path]:
    out: List[Path] = []
    for p in root.rglob("*.md"):
        # Skip anything under a blacklisted directory
        rel_parts = p.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Public — build_graph
# ---------------------------------------------------------------------------


def build_graph(vault_root: Path, output_path: Path) -> Dict[str, Any]:
    """Walk the vault, build a graph, write graph.json. Returns a stats
    dict {nodes, edges, communities, written_to, duration_seconds, errors}.

    Wikilinks that don't resolve (target file doesn't exist) are silently
    skipped — vault notes routinely reference future-or-imagined notes.
    """
    t0 = time.time()
    vault_root = vault_root.resolve()
    if not vault_root.exists():
        return {"error": f"vault not found: {vault_root}", "nodes": 0, "edges": 0}

    files = _walk_vault(vault_root)
    nodes: List[Dict[str, Any]] = []
    label_to_id: Dict[str, str] = {}    # lowercased label → node id
    file_basename_to_id: Dict[str, str] = {}   # basename without .md → id
    raw_records: List[Dict[str, Any]] = []     # parsed records keyed by node id
    errors: int = 0

    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            errors += 1
            continue
        fm, body = _parse_frontmatter(text)
        rel = str(f.relative_to(vault_root)).replace("\\", "/")
        nid = _node_id(rel)
        # Label preference: H1 in body → frontmatter title → filename stem
        label = ""
        h1 = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
        if h1:
            label = h1.group(1).strip()
        if not label:
            label = str(fm.get("title", "")).strip()
        if not label:
            label = f.stem
        # Tags can be a list or a string in frontmatter
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        community = _community_for_path(rel)

        node = {
            "id":          nid,
            "label":       label,
            "source_file": rel,
            "community":   community,
            "tags":        list(tags),
            "type":        str(fm.get("type", "")) or None,
        }
        nodes.append(node)
        label_to_id[label.lower()] = nid
        file_basename_to_id[f.stem.lower()] = nid

        raw_records.append({
            "id":       nid,
            "fm":       fm,
            "body":     body,
            "rel":      rel,
        })

    # Build edges
    links: List[Dict[str, Any]] = []
    seen_edges: set = set()

    def _add_edge(src: str, tgt: str, relation: str, confidence: float = 1.0) -> None:
        if not src or not tgt or src == tgt:
            return
        key = (src, tgt, relation)
        if key in seen_edges:
            return
        seen_edges.add(key)
        links.append({
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": confidence,
            "confidence_score": int(confidence * 100),
        })

    def _resolve(target: str) -> Optional[str]:
        """Match a wikilink target ('Foo' or 'Foo.md' or 'subdir/Foo')
        against known nodes. Order:
            1. Exact label match (case-insensitive)
            2. Exact basename match (case-insensitive)
            3. None — dangling reference, skip silently
        """
        if not target:
            return None
        t_lower = target.lower().strip()
        if t_lower.endswith(".md"):
            t_lower = t_lower[:-3]
        # Path-shaped target — strip everything but the basename
        if "/" in t_lower:
            t_lower = t_lower.rsplit("/", 1)[-1]
        return label_to_id.get(t_lower) or file_basename_to_id.get(t_lower)

    for rec in raw_records:
        nid = rec["id"]
        fm = rec["fm"]
        body = rec["body"]

        # Wikilinks in body → relation: wikilink, confidence 1.0
        for target in _extract_wikilinks(body):
            tid = _resolve(target)
            if tid:
                _add_edge(nid, tid, "wikilink", confidence=1.0)

        # Frontmatter `parent: [[X]]` → relation: parent (high confidence)
        parent_val = fm.get("parent")
        if parent_val:
            parent_targets = parent_val if isinstance(parent_val, list) else [parent_val]
            for pt in parent_targets:
                pt = str(pt).strip()
                # Strip [[ ]] if present
                if pt.startswith("[[") and pt.endswith("]]"):
                    pt = pt[2:-2].split("|")[0].strip()
                tid = _resolve(pt)
                if tid:
                    _add_edge(nid, tid, "parent", confidence=1.0)

        # Frontmatter `related: [[X]]` (list) → relation: related (medium)
        related_val = fm.get("related")
        if related_val:
            related_targets = related_val if isinstance(related_val, list) else [related_val]
            for rt in related_targets:
                rt = str(rt).strip()
                if rt.startswith("[[") and rt.endswith("]]"):
                    rt = rt[2:-2].split("|")[0].strip()
                tid = _resolve(rt)
                if tid:
                    _add_edge(nid, tid, "related", confidence=0.6)

    graph = {
        "nodes": nodes,
        "links": links,
        "metadata": {
            "generator": "openjarvis.tools.vault_graph",
            "generator_version": 1,
            "vault_root": str(vault_root),
            "built_at": datetime.now().isoformat(timespec="seconds"),
            "node_count": len(nodes),
            "edge_count": len(links),
        },
    }

    # Atomic write
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    tmp.replace(output_path)

    duration = time.time() - t0
    communities = len({n["community"] for n in nodes})

    logger.info(
        "vault_graph: built %d nodes, %d edges, %d communities from %s in %.2fs",
        len(nodes), len(links), communities, vault_root, duration,
    )

    return {
        "nodes":            len(nodes),
        "edges":            len(links),
        "communities":      communities,
        "errors":           errors,
        "duration_seconds": round(duration, 2),
        "written_to":       str(output_path),
    }


__all__ = ["build_graph"]
