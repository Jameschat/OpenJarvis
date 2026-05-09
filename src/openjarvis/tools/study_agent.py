"""Nightly study-agent — uses qwen3:32b (local, free) to build domain
knowledge in the vault.

Picks one topic per night from `Brain/Curriculum/<discipline>.md`, runs a
structured prompt against qwen3:32b via the LiteLLM proxy, writes a study
note to `Brain/Study/<discipline>/<date> - <slug>.md`.

State lives in `Brain/Curriculum/_state.json`. **Do not hand-edit.**

v1 scope (this file): topic picker + state I/O. The session runner +
agent_runner registration land in subsequent tasks.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Topic:
    discipline: str
    slug: str
    description: str
    status: str  # "unvisited" | "revisit"


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def _state_path(vault_root: Path) -> Path:
    return vault_root / "Curriculum" / "_state.json"


def load_state(vault_root: Path) -> Dict[str, Any]:
    path = _state_path(vault_root)
    if not path.exists():
        return {"version": 1, "disciplines": {}, "rotation_history": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: Dict[str, Any], vault_root: Path) -> None:
    path = _state_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write — never leave a partial state file.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def record_studied(
    *,
    discipline: str,
    topic_slug: str,
    vault_root: Path,
    now: datetime,
) -> None:
    """Mark a topic as studied at `now`; append to rotation history."""
    state = load_state(vault_root)
    disc = state["disciplines"].setdefault(
        discipline, {"studied": {}, "last_rotation_index": 0}
    )
    disc["studied"][topic_slug] = now.isoformat()
    state["rotation_history"].append({
        "discipline": discipline,
        "topic": topic_slug,
        "started_at": now.isoformat(),
    })
    save_state(state, vault_root)


# ---------------------------------------------------------------------------
# Curriculum parsing
# ---------------------------------------------------------------------------


# Matches: `1. some-slug: free-form description`
_TOPIC_LINE = re.compile(
    r"^\s*\d+\.\s+(?P<slug>[a-z0-9-]+)\s*:\s*(?P<desc>.+?)\s*$"
)
# Matches the `revisit_after_days: <int>` frontmatter line
_REVISIT_LINE = re.compile(r"^\s*revisit_after_days\s*:\s*(\d+)\s*$")


def _parse_curriculum(path: Path) -> tuple[List[Topic], int]:
    """Return (topics_in_file_order, revisit_after_days)."""
    text = path.read_text(encoding="utf-8")
    revisit_days = 30  # sane default if frontmatter missing
    topics: List[Topic] = []
    in_frontmatter = False
    for line in text.splitlines():
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            m = _REVISIT_LINE.match(line)
            if m:
                revisit_days = int(m.group(1))
            continue
        m = _TOPIC_LINE.match(line)
        if m:
            topics.append(Topic(
                discipline=path.stem,  # filename without .md
                slug=m.group("slug"),
                description=m.group("desc"),
                status="unvisited",  # caller may overwrite to "revisit"
            ))
    return topics, revisit_days


# ---------------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------------


def pick_next_topic(
    discipline: str,
    vault_root: Path,
    *,
    now: datetime,
) -> Optional[Topic]:
    """Return next topic to study for `discipline`, or None.

    Rules:
      1. First topic in curriculum order not present in state.studied → unvisited
      2. All studied: oldest-studied topic whose age > revisit_after_days → revisit
      3. Otherwise: None (caller should rotate to next discipline)
    """
    curriculum_path = vault_root / "Curriculum" / f"{discipline}.md"
    if not curriculum_path.exists():
        return None
    topics, revisit_days = _parse_curriculum(curriculum_path)
    if not topics:
        return None

    state = load_state(vault_root)
    studied = (
        state.get("disciplines", {})
        .get(discipline, {})
        .get("studied", {})
    )

    # Rule 1: first unvisited
    for t in topics:
        if t.slug not in studied:
            return t

    # Rule 2: oldest revisit-eligible
    threshold = now - timedelta(days=revisit_days)
    eligible: List[tuple[datetime, Topic]] = []
    for t in topics:
        ts_str = studied.get(t.slug)
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < threshold:
            eligible.append((ts, t))
    if eligible:
        eligible.sort(key=lambda x: x[0])
        oldest = eligible[0][1]
        return Topic(
            discipline=oldest.discipline,
            slug=oldest.slug,
            description=oldest.description,
            status="revisit",
        )

    # Rule 3: nothing to do
    return None


__all__ = [
    "Topic",
    "load_state",
    "save_state",
    "record_studied",
    "pick_next_topic",
]
