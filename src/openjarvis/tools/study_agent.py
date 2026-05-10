"""Nightly study-agent — uses qwen3.6:35b-a3b (local, free) to build domain
knowledge in the vault.

Picks one topic per night from `Brain/Curriculum/<discipline>.md`, runs a
structured prompt against qwen3.6:35b-a3b via the LiteLLM proxy, writes a study
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


# ---------------------------------------------------------------------------
# GPU contention guard
# ---------------------------------------------------------------------------


def is_gpu_busy(*, threshold_pct: int = 80) -> bool:
    """True if VRAM utilisation on GPU 0 is above `threshold_pct`.

    Uses `nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits`.
    Returns False if nvidia-smi is missing — non-GPU hosts (CI, dev laptops)
    should let the rest of the agent run unblocked.
    """
    import subprocess
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
                "--id=0",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != 2:
        return False
    try:
        used = int(parts[0])
        total = int(parts[1])
    except ValueError:
        return False
    if total == 0:
        return False
    pct = (used / total) * 100
    return pct >= threshold_pct


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


_DISCIPLINE_GUIDANCE = {
    "web-dev": (
        "You are a senior web developer mentor. Cite primary sources where "
        "possible: MDN, the W3C spec, and recent (2024+) blog posts from "
        "well-known engineers. Avoid framework hype; explain trade-offs."
    ),
    "game-dev": (
        "You are a senior game developer mentor with cross-engine experience "
        "(Unreal, Unity, Godot). Cite primary engine docs (docs.unrealengine.com, "
        "docs.unity3d.com, docs.godotengine.org) and well-regarded GDC talks "
        "where applicable. Discuss trade-offs honestly — engines have failure "
        "modes."
    ),
    "software-dev": (
        "You are a senior software engineer mentor. Cite original papers, "
        "RFCs, and authoritative documentation. Avoid hype; quantify "
        "trade-offs with rough numbers where possible (latency, memory, ops "
        "complexity)."
    ),
    "intelligence": (
        "You are a senior intelligence analyst / research-methodology "
        "instructor. Cite primary sources (academic papers, government "
        "manuals, foundational books). Emphasise epistemic humility and "
        "structured techniques over confidence-projection."
    ),
}


_OUTPUT_CONTRACT = """\
[OUTPUT CONTRACT]

Produce a markdown study note matching this structure EXACTLY:

## Key concepts

- 4-8 bullets covering the core ideas. Each bullet starts with a bold
  noun phrase, then a one-sentence explanation.

## Worked example

A short concrete example — code block, walk-through, or scenario — that
demonstrates one of the key concepts in action. Real, runnable / verifiable.

## Common pitfalls

- 3-5 bullets covering mistakes a competent-but-new practitioner makes,
  with a one-sentence remedy each.

## Self-grade and reasoning

A single line: `<N>/5 — <one-sentence justification>` where N is your
honest assessment of this note's quality (1=guesswork, 5=confident on
all points, all sources verified).

## References

- 3-6 URLs you would expect to be authoritative for this topic. Bare URLs
  preferred over [text](url) form so the operator can spot-check them.

[FORBIDDEN]
- Hedging without justification ("might", "could be" without why).
- Inventing URLs you don't actually believe exist.
- Padding — if you don't have 8 key concepts, give 4.
"""


def build_study_prompt(topic: Topic) -> str:
    guidance = _DISCIPLINE_GUIDANCE.get(
        topic.discipline,
        "You are a senior subject-matter expert. Cite primary sources.",
    )
    framing = (
        "This is a REVISIT of a topic studied previously — focus on what's "
        "changed in the field over the past 30+ days OR a deeper layer not "
        "covered last time. Do not simply restate basics."
        if topic.status == "revisit"
        else "This is a fresh topic — assume the reader is competent in "
             "adjacent areas but not this specific one."
    )
    return (
        f"[PERSONA]\n{guidance}\n\n"
        f"[ROLE]\n{framing}\n\n"
        f"[TOPIC]\n"
        f"discipline: {topic.discipline}\n"
        f"slug: {topic.slug}\n"
        f"focus: {topic.description}\n\n"
        f"{_OUTPUT_CONTRACT}"
    )


# ---------------------------------------------------------------------------
# Note writer
# ---------------------------------------------------------------------------


_GRADE_LINE = re.compile(r"(?P<n>[1-5])\s*/\s*5\b")


def _extract_self_grade(body: str) -> int:
    """Pull `<N>/5` from the Self-grade section. 0 = unparseable."""
    # Search only the section after "Self-grade" if present, else whole body.
    section = body
    if "Self-grade" in body or "self-grade" in body:
        # Take everything from the first match of the Self-grade heading on.
        idx = body.lower().find("self-grade")
        section = body[idx:]
    m = _GRADE_LINE.search(section)
    return int(m.group("n")) if m else 0


def write_study_note(
    *,
    topic: Topic,
    body: str,
    vault_root: Path,
    now: datetime,
) -> Path:
    """Write a study note, returning the written path.

    Path: `<vault_root>/Study/<discipline>/<YYYY-MM-DD> - <slug>.md`
    """
    date_str = now.strftime("%Y-%m-%d")
    target_dir = vault_root / "Study" / topic.discipline
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{date_str} - {topic.slug}.md"
    grade = _extract_self_grade(body)
    frontmatter = (
        "---\n"
        "type: study\n"
        f"date: {date_str}\n"
        f"discipline: {topic.discipline}\n"
        f"topic: {topic.slug}\n"
        "model: qwen3.6:35b-a3b\n"
        f"status: {topic.status}\n"
        f"self_grade: {grade}\n"
        "---\n\n"
        f"# Study — {topic.slug}\n\n"
        f"**Focus:** {topic.description}\n\n"
    )
    footer = "\n\n_Compiled by `study-agent` (qwen3.6:35b-a3b via LiteLLM)._\n"
    path.write_text(frontmatter + body.strip() + footer, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Discipline rotation + agent entry point
# ---------------------------------------------------------------------------


_DISCIPLINE_ORDER = ["web-dev", "game-dev", "software-dev", "intelligence"]


def _pick_next_discipline(state: Dict[str, Any]) -> str:
    history = state.get("rotation_history") or []
    if not history:
        return _DISCIPLINE_ORDER[0]
    last = history[-1]["discipline"]
    try:
        idx = _DISCIPLINE_ORDER.index(last)
    except ValueError:
        return _DISCIPLINE_ORDER[0]
    return _DISCIPLINE_ORDER[(idx + 1) % len(_DISCIPLINE_ORDER)]


def _vault_root() -> Path:
    """Resolve vault root from the existing obsidian_brain helper."""
    from openjarvis.tools import obsidian_brain as ob
    return ob.BRAIN_ROOT


def _call_qwen(prompt: str, *, max_tokens: int = 4000) -> Optional[str]:
    """Call qwen3.6:35b-a3b via the LiteLLM proxy. Returns body or None on error.

    Timeout 600s. Empirical baseline (2026-05-09): qwen3:32b at Q4_K_M on RTX 4090
    with thinking-mode enabled runs ~24 tok/s end-to-end. A 4000-token
    study note (including ~1500 reasoning tokens burned silently) lands
    in 150-250s; long topics or first-call-after-eviction can push to
    400s+. Don't shrink this without first disabling thinking-mode via
    the qwen3 chat-template flag.
    """
    import os
    try:
        from openai import OpenAI
    except ImportError:
        return None
    base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:4000")
    # The proxy ignores the API key for the local model, but the OpenAI
    # client SDK still requires *some* string. Use whatever is set; if
    # nothing is set, "sk-noop" works against LiteLLM for local models.
    api_key = os.environ.get("OPENAI_API_KEY", "sk-noop")
    client = OpenAI(base_url=base_url, api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model="qwen3.6-35b-a3b-local",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Produce the study note now."},
            ],
            max_tokens=max_tokens,
            timeout=600,
        )
    except Exception:
        return None
    if not resp.choices:
        return None
    return resp.choices[0].message.content


def run_as_agent_task(prompt: str = "") -> Dict[str, Any]:
    """Entry point for agent_runner's python provider.

    Returns a dict the agent_runner records as the task result. Never raises
    — failures become structured 'skipped' / 'error' results.
    """
    now = datetime.now(timezone.utc)
    if is_gpu_busy():
        return {"ok": False, "skipped": True, "reason": "gpu busy"}

    vault = _vault_root()
    state = load_state(vault)
    started_disc = _pick_next_discipline(state)
    chosen: Optional[Topic] = None
    for offset in range(len(_DISCIPLINE_ORDER)):
        idx = (_DISCIPLINE_ORDER.index(started_disc) + offset) % len(_DISCIPLINE_ORDER)
        disc = _DISCIPLINE_ORDER[idx]
        candidate = pick_next_topic(disc, vault, now=now)
        if candidate is not None:
            chosen = candidate
            break
    if chosen is None:
        return {"ok": False, "skipped": True, "reason": "all disciplines exhausted"}

    body = _call_qwen(build_study_prompt(chosen))
    if not body:
        return {
            "ok": False,
            "error": "qwen call failed (proxy down? rate limit? timeout?)",
            "discipline": chosen.discipline,
            "topic": chosen.slug,
        }

    path = write_study_note(topic=chosen, body=body, vault_root=vault, now=now)
    record_studied(
        discipline=chosen.discipline,
        topic_slug=chosen.slug,
        vault_root=vault,
        now=now,
    )
    return {
        "ok": True,
        "discipline": chosen.discipline,
        "topic": chosen.slug,
        "status": chosen.status,
        "path": str(path),
    }


__all__ = [
    "Topic",
    "load_state",
    "save_state",
    "record_studied",
    "pick_next_topic",
    "is_gpu_busy",
    "build_study_prompt",
    "write_study_note",
    "run_as_agent_task",
]
