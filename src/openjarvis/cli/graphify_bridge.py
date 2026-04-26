"""In-process bridge to the graphify knowledge graph.

The graphify CLI builds a graph from the operator's Obsidian vault and
saves it to ``graphify-out/graph.json``. This module loads that graph
once, caches it (with mtime check for staleness), and exposes three
operations the LLM tool-use brain can call:

  * ``query(question, mode="bfs", depth=3)`` — find nodes whose label
    matches the question, traverse outward, return ranked subgraph.
    Use for "how does X relate to Y", "what's connected to Z", etc.
  * ``path(a, b)`` — shortest path between two named concepts. Use for
    "how does X reach Y" / "what bridges X and Y".
  * ``explain(node)`` — dump everything connected to a single node.
    Use for "tell me what X is and what it touches".

Everything runs without spawning subprocesses. Fast, no auth, no
network. If the graph isn't present (graphify never run), the
operations return a structured "not available" response instead of
crashing.
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


__all__ = ["status", "query", "path", "explain"]
