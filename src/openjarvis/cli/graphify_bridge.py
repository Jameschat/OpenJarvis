"""In-process bridge to the graphify knowledge graph.

The graphify CLI builds a graph from the operator's Obsidian vault and
saves it to ``graphify-out/graph.json``. This module loads that graph
once, caches it (with mtime check for staleness), and exposes:

  * ``query(question, mode="bfs", depth=3)`` — find nodes whose label
    matches the question, traverse outward, return ranked subgraph.
    Use for "how does X relate to Y", "what's connected to Z", etc.
  * ``path(a, b)`` — shortest path between two named concepts. Use for
    "how does X reach Y" / "what bridges X and Y".
  * ``explain(node)`` — dump everything connected to a single node.
    Use for "tell me what X is and what it touches".
  * ``refresh()`` — kick off graphify in a background subprocess to
    rebuild the graph from the live vault. Non-blocking; the next
    query that lands after the rebuild auto-loads the new graph via
    mtime check.
  * ``note_vault_write()`` / ``staleness()`` — track how many vault
    writes have happened since the last refresh, so the HUD can show
    a STALE indicator and the operator can refresh on demand.

Read ops have zero side effects. Refresh runs the actual graphify CLI
externally — never inline — so a long-running rebuild can't poison
the Jarvis process.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph location + lazy load
# ---------------------------------------------------------------------------

def _graphify_dir() -> Path:
    env = os.environ.get("OPENJARVIS_GRAPHIFY_DIR", "").strip()
    if env:
        return Path(env)
    return Path(r"E:\Claude\Brain-Graphs\graphify-out")


def _graph_file() -> Path:
    return _graphify_dir() / "graph.json"


_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_mtime: float = 0.0
# Built indices for fast lookup
_nodes_by_id: Dict[str, Dict[str, Any]] = {}
_adj: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}  # undirected adjacency
_label_to_id: Dict[str, str] = {}                       # lowercase label -> id


def _build_indices(graph: Dict[str, Any]) -> None:
    global _nodes_by_id, _adj, _label_to_id
    _nodes_by_id = {}
    _adj = {}
    _label_to_id = {}
    for n in graph.get("nodes") or []:
        nid = n.get("id")
        if not nid:
            continue
        _nodes_by_id[nid] = n
        _adj.setdefault(nid, [])
        label = (n.get("label") or "").strip().lower()
        if label and label not in _label_to_id:
            _label_to_id[label] = nid
    for e in graph.get("links") or graph.get("edges") or []:
        s = e.get("source")
        t = e.get("target")
        if not s or not t or s not in _nodes_by_id or t not in _nodes_by_id:
            continue
        _adj.setdefault(s, []).append((t, e))
        _adj.setdefault(t, []).append((s, e))


def _load(force: bool = False) -> Optional[Dict[str, Any]]:
    """Load + index the graph, with mtime cache. Returns None if no graph."""
    global _cache, _cache_mtime
    p = _graph_file()
    if not p.exists():
        return None
    mtime = p.stat().st_mtime
    with _lock:
        if force or _cache is None or mtime > _cache_mtime:
            try:
                _cache = json.loads(p.read_text(encoding="utf-8"))
                _cache_mtime = mtime
                _build_indices(_cache)
                logger.info("graphify_bridge: loaded %d nodes, %d edges from %s",
                            len(_nodes_by_id), sum(len(v) for v in _adj.values()) // 2, p)
            except Exception as exc:
                logger.exception("graphify_bridge: failed to load graph: %s", exc)
                return None
        return _cache


# ---------------------------------------------------------------------------
# Node matching — fuzzy by label
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def _score_node(node: Dict[str, Any], query_tokens: List[str]) -> int:
    label = (node.get("label") or "").lower()
    nid = (node.get("id") or "").lower()
    score = 0
    for t in query_tokens:
        if not t or len(t) < 2:
            continue
        if t in label:
            score += 3
        if t in nid:
            score += 1
    return score


def _find_nodes(query: str, top_n: int = 3) -> List[str]:
    """Return up to top_n node ids whose labels match the query best."""
    if not _nodes_by_id:
        return []
    qt = _tokens(query)
    if not qt:
        return []
    # Exact label match wins
    direct = _label_to_id.get(query.strip().lower())
    if direct:
        return [direct]
    scored = []
    for nid, node in _nodes_by_id.items():
        s = _score_node(node, qt)
        if s > 0:
            scored.append((s, nid))
    scored.sort(reverse=True)
    return [nid for _, nid in scored[:top_n]]


def _node_brief(node: Dict[str, Any]) -> Dict[str, Any]:
    """Compact node dict suitable for tool output."""
    return {
        "id": node.get("id"),
        "label": node.get("label"),
        "source_file": node.get("source_file"),
        "community": node.get("community"),
    }


def _edge_brief(s: str, t: str, edge: Dict[str, Any]) -> Dict[str, Any]:
    sl = (_nodes_by_id.get(s, {}) or {}).get("label") or s
    tl = (_nodes_by_id.get(t, {}) or {}).get("label") or t
    return {
        "from": sl,
        "to": tl,
        "relation": edge.get("relation"),
        "confidence": edge.get("confidence"),
        "score": edge.get("confidence_score"),
    }


# ---------------------------------------------------------------------------
# Public ops
# ---------------------------------------------------------------------------

def status() -> Dict[str, Any]:
    """Tiny info dict the LLM can use to decide whether the graph is usable."""
    g = _load()
    if g is None:
        return {"available": False, "reason": "no graph.json — run `graphify <vault path>` first"}
    return {
        "available": True,
        "nodes": len(_nodes_by_id),
        "edges": sum(len(v) for v in _adj.values()) // 2,
        "communities": len({(n.get("community")) for n in _nodes_by_id.values() if n.get("community") is not None}),
        "graph_dir": str(_graphify_dir()),
    }


def query(question: str, mode: str = "bfs", depth: int = 3,
          max_nodes: int = 30) -> Dict[str, Any]:
    """Traverse the graph from the best-matching nodes for the question.

    mode: 'bfs' = explore neighbours layer by layer (broad context).
          'dfs' = follow one chain deep first (trace a path).
    """
    g = _load()
    if g is None:
        return {"error": "graph not available", "hint": "run `graphify <vault path>` first"}
    starts = _find_nodes(question, top_n=3)
    if not starts:
        return {"hits": [], "note": f"no nodes match query terms in {question!r}"}

    visited = set(starts)
    edges_seen: List[Tuple[str, str, Dict[str, Any]]] = []

    if mode == "dfs":
        stack = [(s, 0) for s in reversed(starts)]
        while stack and len(visited) < max_nodes:
            node, d = stack.pop()
            if d >= depth:
                continue
            for nb, edge in _adj.get(node, []):
                if nb not in visited:
                    visited.add(nb)
                    edges_seen.append((node, nb, edge))
                    stack.append((nb, d + 1))
                    if len(visited) >= max_nodes:
                        break
    else:
        # BFS
        frontier = deque(starts)
        layer = {s: 0 for s in starts}
        while frontier and len(visited) < max_nodes:
            node = frontier.popleft()
            d = layer[node]
            if d >= depth:
                continue
            for nb, edge in _adj.get(node, []):
                if nb not in visited:
                    visited.add(nb)
                    layer[nb] = d + 1
                    edges_seen.append((node, nb, edge))
                    frontier.append(nb)
                    if len(visited) >= max_nodes:
                        break

    # Score nodes by query-term overlap so the model sees the most relevant first
    qt = _tokens(question)
    ranked = sorted(visited,
                    key=lambda nid: _score_node(_nodes_by_id[nid], qt),
                    reverse=True)
    return {
        "started_from": [_nodes_by_id[s].get("label") for s in starts],
        "mode": mode,
        "depth": depth,
        "node_count": len(ranked),
        "nodes": [_node_brief(_nodes_by_id[nid]) for nid in ranked],
        "edges": [_edge_brief(s, t, e) for s, t, e in edges_seen[:50]],
    }


def path(a: str, b: str) -> Dict[str, Any]:
    """Shortest path between two named concepts."""
    g = _load()
    if g is None:
        return {"error": "graph not available"}
    a_ids = _find_nodes(a, top_n=1)
    b_ids = _find_nodes(b, top_n=1)
    if not a_ids:
        return {"error": f"no node matches {a!r}"}
    if not b_ids:
        return {"error": f"no node matches {b!r}"}
    src, tgt = a_ids[0], b_ids[0]
    if src == tgt:
        return {"hops": 0, "path": [_node_brief(_nodes_by_id[src])]}

    # BFS for unweighted shortest path
    prev: Dict[str, Optional[str]] = {src: None}
    edge_in: Dict[str, Optional[Dict[str, Any]]] = {src: None}
    q = deque([src])
    while q:
        node = q.popleft()
        if node == tgt:
            break
        for nb, edge in _adj.get(node, []):
            if nb not in prev:
                prev[nb] = node
                edge_in[nb] = edge
                q.append(nb)
    if tgt not in prev:
        return {
            "error": f"no path between {_nodes_by_id[src].get('label')} and {_nodes_by_id[tgt].get('label')}",
        }

    # Reconstruct
    chain: List[str] = []
    cur: Optional[str] = tgt
    while cur is not None:
        chain.append(cur)
        cur = prev.get(cur)
    chain.reverse()
    out_nodes = [_node_brief(_nodes_by_id[nid]) for nid in chain]
    out_edges = []
    for i in range(len(chain) - 1):
        e = edge_in.get(chain[i + 1])
        if e:
            out_edges.append(_edge_brief(chain[i], chain[i + 1], e))
    return {
        "hops": len(chain) - 1,
        "from": _nodes_by_id[src].get("label"),
        "to":   _nodes_by_id[tgt].get("label"),
        "path": out_nodes,
        "edges": out_edges,
    }


def explain(node: str) -> Dict[str, Any]:
    """Everything connected to a single node."""
    g = _load()
    if g is None:
        return {"error": "graph not available"}
    matches = _find_nodes(node, top_n=1)
    if not matches:
        return {"error": f"no node matches {node!r}"}
    nid = matches[0]
    n = _nodes_by_id[nid]
    neighbours = []
    for nb, edge in _adj.get(nid, []):
        nb_node = _nodes_by_id.get(nb, {})
        neighbours.append({
            "label": nb_node.get("label"),
            "relation": edge.get("relation"),
            "confidence": edge.get("confidence"),
            "source_file": nb_node.get("source_file"),
        })
    return {
        "node": _node_brief(n),
        "degree": len(neighbours),
        "neighbours": neighbours[:40],
    }


# ---------------------------------------------------------------------------
# Staleness tracking + background refresh
# ---------------------------------------------------------------------------

_writes_since_refresh = 0
_last_refresh_started: float = 0.0
_refresh_proc: Any = None
_refresh_lock = threading.Lock()


def note_vault_write() -> None:
    """Called by obsidian_brain on every vault write so we can show a
    'STALE' indicator and prompt a refresh when enough has changed."""
    global _writes_since_refresh
    with _refresh_lock:
        _writes_since_refresh += 1


def staleness() -> Dict[str, Any]:
    """Summary the HUD or LLM can use to decide whether to refresh."""
    with _refresh_lock:
        writes = _writes_since_refresh
        last_started = _last_refresh_started
        proc = _refresh_proc
    p = _graph_file()
    age_seconds = None
    if p.exists():
        import time
        age_seconds = int(time.time() - p.stat().st_mtime)
    refreshing = bool(proc and proc.poll() is None)
    return {
        "writes_since_refresh": writes,
        "graph_age_seconds": age_seconds,
        "refresh_in_progress": refreshing,
        "last_refresh_started": last_started,
    }


def _vault_root() -> Path:
    return Path(os.environ.get(
        "OPENJARVIS_GRAPHIFY_VAULT_ROOT",
        r"E:\Claude\Obsidian\Claude\Brain",
    ))


def _vault_subfolders() -> List[str]:
    raw = os.environ.get(
        "OPENJARVIS_GRAPHIFY_SUBFOLDERS",
        "Knowledge,Projects,Decisions,People,Daily,Content",
    )
    return [s.strip() for s in raw.split(",") if s.strip()]


def _staging_dir() -> Path:
    return Path(os.environ.get(
        "OPENJARVIS_GRAPHIFY_STAGING",
        str(_graphify_dir().parent / "source"),
    ))


def _stage_vault_subset() -> int:
    """Mirror the chosen vault subfolders into the staging dir. Returns
    file count. Best-effort copy — overwrites destination."""
    import shutil
    src = _vault_root()
    dst = _staging_dir()
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for sub in _vault_subfolders():
        sp = src / sub
        if not sp.exists():
            continue
        shutil.copytree(sp, dst / sub)
        n += sum(1 for _ in (dst / sub).rglob("*.md"))
    return n


def refresh(blocking: bool = False) -> Dict[str, Any]:
    """Kick off graphify against the vault subset in a background
    subprocess. Returns immediately unless blocking=True. Resets the
    write-staleness counter optimistically (it'll be accurate by the
    time the subprocess finishes)."""
    global _writes_since_refresh, _last_refresh_started, _refresh_proc
    import subprocess
    import sys
    import time

    with _refresh_lock:
        if _refresh_proc and _refresh_proc.poll() is None:
            return {"started": False, "reason": "refresh already in progress"}
        try:
            file_count = _stage_vault_subset()
        except Exception as exc:
            logger.exception("graphify_bridge: staging copy failed")
            return {"started": False, "reason": f"staging copy failed: {exc}"}
        if file_count == 0:
            return {"started": False, "reason": "no files in staging — check vault path / subfolders"}

        # Find the graphify executable. uv tool install put it under
        # ~/.local/bin on Windows, plus 'graphify' should be on PATH if
        # the user ran update-shell.
        candidates = [
            os.environ.get("OPENJARVIS_GRAPHIFY_BIN", ""),
            str(Path.home() / ".local" / "bin" / "graphify.exe"),
            str(Path.home() / ".local" / "bin" / "graphify"),
            "graphify",
        ]
        exe = next((c for c in candidates if c and (Path(c).exists() if "/" in c or "\\" in c else True)), "graphify")

        # Run from the parent of graphify-out so outputs land in the
        # expected location.
        cwd = _graphify_dir().parent
        cwd.mkdir(parents=True, exist_ok=True)
        log_path = _graphify_dir().parent / "refresh.log"
        try:
            log_fh = log_path.open("ab")
            _refresh_proc = subprocess.Popen(
                [exe, str(_staging_dir())],
                cwd=str(cwd),
                stdout=log_fh,
                stderr=log_fh,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        except Exception as exc:
            logger.exception("graphify_bridge: failed to spawn refresh")
            return {"started": False, "reason": f"spawn failed: {exc}"}

        _last_refresh_started = time.time()
        _writes_since_refresh = 0

    if blocking:
        try:
            _refresh_proc.wait(timeout=600)
        except Exception:
            pass
    return {
        "started": True,
        "files_staged": file_count,
        "log": str(log_path),
        "blocking": blocking,
    }


__all__ = ["status", "query", "path", "explain",
           "note_vault_write", "staleness", "refresh"]
