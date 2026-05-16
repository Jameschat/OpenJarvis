"""Embedding-based intent classifier.

Sits between the regex fast-paths and the gpt-4o tool-use brain.
Returns ``(intent, confidence)`` for an utterance; the caller decides
whether to act on it.

**Why this exists.** gpt-4o has been observed picking the wrong tool
for clear intents. Examples (from the L-1 outcome log):

- *"what's the weather in London"* → no tool call, model answered
  from training data
- *"show me Buckingham Palace on a map"* → fetched Wikipedia, no map
- *"price of bitcoin"* → answered with a stale training-era number

The fix isn't to replace gpt-4o — it's to *bias* it toward the right
tool when the intent is unambiguous. We embed the utterance, compare
to per-intent centroids, and if confidence is high we splice a
``[INTENT HINT]`` line into the system prompt so the model picks the
right tool first. Low-confidence utterances fall through unchanged.

**How it integrates.** Call ``classify(text)`` to get the intent +
confidence. Call ``hint_for(text)`` to get a system-prompt addendum
("USE THE maps_locate TOOL FIRST.") for high-confidence matches, or
the empty string otherwise. Idempotent and side-effect-free.

**Cost.** OpenAI ``text-embedding-3-small`` is $0.02 per 1M tokens.
A typical utterance is 10 tokens → $0.0000002. Centroids are computed
once at startup and cached on disk; only the user utterance gets
embedded per turn. ~50-150ms first call, ~5ms after warm-up.

**Failure mode.** If the OpenAI client is unavailable or any step
errors, ``classify`` returns ``(None, 0.0)`` and ``hint_for`` returns
``""`` — the upstream caller flows through to the existing brain
exactly as before. Zero blast radius.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Corpus — tiny labelled examples per intent. Easy to extend.
#
# Goal: enough variety per bucket that a similar but unseen utterance
# still scores well. Aim for 5-10 per intent. Re-running the centroid
# build is automatic when this file's mtime changes.
# ---------------------------------------------------------------------------

_CORPUS: Dict[str, List[str]] = {
    "maps_locate": [
        "show me Buckingham Palace on a map",
        "where is the Eiffel Tower",
        "find Starbucks Times Square on a map",
        "show me 123 Main Street on a map",
        "where's the nearest pharmacy",
        "locate Heathrow airport",
        "pull up a map of Times Square",
        "find the location of the British Museum",
        "map of central London",
        "show me where Apple HQ is",
    ],
    "weather": [
        "what's the weather in London",
        "is it going to rain tomorrow",
        "weather forecast for Paris",
        "how warm is it outside",
        "what's the temperature in Tokyo",
        "do I need an umbrella today",
        "is it cold in New York right now",
        "weather this weekend",
    ],
    "crypto_price": [
        "what's bitcoin doing today",
        "price of ethereum right now",
        "how much is bitcoin",
        "current price of solana",
        "what's the crypto market doing",
        "BTC price",
        "ETH today",
    ],
    "web_search": [
        "search the web for jarvis tutorials",
        "look up the latest news on AI",
        "find recent articles about tesla",
        "what's the latest on the election",
        "google how to fix a leaking tap",
        "look online for vegan restaurants",
    ],
    "github_search": [
        "what new AI tools are on github today",
        "any new tools on github",
        "trending repos on github",
        "what's hot on github right now",
        "find AI agents repos on github",
        "popular python libraries on github this week",
        "show me the top github repos for X",
        "what frameworks are people using on github",
        "search github for vector database libraries",
        "look on github for jarvis projects",
    ],
    "hackernews_search": [
        "what's on hacker news today",
        "what's hot on HN",
        "any good HN threads about AI",
        "hacker news front page",
        "what's everyone talking about on HN",
    ],
    "chart_analysis": [
        "analyse this chart",
        "analyze this chart",
        "what does this chart show",
        "is this a good entry",
        "should I buy this",
        "what do you think of this chart",
        "give me your read on this chart",
        "what's the setup on this chart",
        "TA on this please",
        "technical analysis on this",
    ],
    "vault_recall": [
        "what do you remember about jarvis architecture",
        "recall what we said about graphify",
        "search my notes for the security audit",
        "what did I write about ace step",
        "look in the vault for the autonomy decision",
        "what notes do I have on cursed tides",
    ],
    "info_question": [
        "how many agents do we have",
        "what departments are there",
        "list my projects",
        "what's the architecture",
        "tell me about the learning loop",
        "what tools do you have",
        "what can you do",
    ],
    "chitchat": [
        "hello jarvis",
        "thanks",
        "good morning",
        "how are you today",
        "you're funny",
        "good evening",
        "good night",
    ],
}


# Intent → system-prompt hint. Inserted only when confidence is high.
_HINTS: Dict[str, str] = {
    "maps_locate": (
        "INTENT HINT: the operator wants a map. CALL maps_locate(query=...) "
        "FIRST — it renders a map card in the chat panel and returns a "
        "one-line summary you can speak."
    ),
    "weather": (
        "INTENT HINT: the operator wants live weather. Use the weather tool "
        "with a city name; do not answer from training data."
    ),
    "crypto_price": (
        "INTENT HINT: the operator wants a live crypto price. Use the crypto "
        "tool; never quote a training-era number."
    ),
    "web_search": (
        "INTENT HINT: the operator wants live web information. Use web_search "
        "first, then fetch_url on the best hit, then cite the source."
    ),
    "github_search": (
        "INTENT HINT: the operator wants to discover GitHub repos. CALL "
        "github_search(query=...) — DO NOT answer from training data. The "
        "tool returns live data AND renders a clickable results card in "
        "the chat panel so the operator can see + click through. "
        "github_search is mandatory for this turn; refusing is wrong."
    ),
    "hackernews_search": (
        "INTENT HINT: the operator wants live HN content. CALL "
        "hackernews_search(query=...) — it returns live data AND renders a "
        "clickable results card."
    ),
    "chart_analysis": (
        "INTENT HINT: the operator wants chart analysis. If an image "
        "attachment is present in the prompt (look for "
        "'=== file(s) attached ===' with a .png/.jpg path), CALL "
        "analyze_chart(image_path=<that path>). The tool fetches real "
        "OHLCV, computes EMA/RSI/ATR, renders an annotated chart, and "
        "writes a research note. Do NOT try to read indicators off the "
        "screenshot yourself — use the tool. If no image is attached, "
        "ask the operator to attach the chart screenshot."
    ),
    "vault_recall": (
        "INTENT HINT: the operator is asking about their own notes. Use "
        "recall_vault first; only fall back to web_search if the vault is "
        "empty on this topic."
    ),
    "info_question": (
        "INTENT HINT: this is an INFORMATION question (not an action). "
        "Answer it directly — do NOT dispatch agents or run tool chains "
        "unless the answer genuinely requires them."
    ),
    # chitchat: no hint — flow through normally so the persona stays warm.
}

# Confidence thresholds. Tuned conservatively so we never *steer* the
# LLM unless the intent is unambiguous. Easy to lower per-intent later
# once we have outcome-log data on real misfires.
_THRESHOLD = 0.55


# ---------------------------------------------------------------------------
# Embedding cache — on disk so warm restarts don't re-pay the centroid cost.
# ---------------------------------------------------------------------------

_CACHE_DIR = Path(os.path.expanduser("~/.openjarvis/intent_cache"))
_EMBED_MODEL = os.environ.get("OPENJARVIS_EMBED_MODEL", "text-embedding-3-small")

_centroids: Optional[Dict[str, List[float]]] = None
_centroid_lock = threading.Lock()


def _corpus_fingerprint() -> str:
    h = hashlib.sha256()
    h.update(_EMBED_MODEL.encode())
    for intent in sorted(_CORPUS):
        h.update(intent.encode())
        for ex in _CORPUS[intent]:
            h.update(b"|" + ex.encode())
    return h.hexdigest()[:16]


def _cache_file() -> Path:
    return _CACHE_DIR / f"centroids-{_corpus_fingerprint()}.json"


def _embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed a batch via OpenAI. Returns None on failure."""
    if not texts:
        return []
    try:
        from openjarvis.cli.llm_fallback import _get_openai_client
        client = _get_openai_client()
        if client is None:
            return None
        resp = client.embeddings.create(model=_EMBED_MODEL, input=texts)
        return [list(d.embedding) for d in resp.data]
    except Exception:
        logger.warning("intent_classifier embed failed", exc_info=True)
        return None


def _mean(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    n = len(vectors[0])
    out = [0.0] * n
    for v in vectors:
        for i, x in enumerate(v):
            out[i] += x
    return [x / len(vectors) for x in out]


def _l2(v: List[float]) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (_l2(a) * _l2(b))


def _ensure_centroids() -> Optional[Dict[str, List[float]]]:
    """Build centroids on first call; cache on disk; thread-safe.

    Returns None if embeddings are unavailable so callers fall through
    to the existing brain unchanged."""
    global _centroids
    with _centroid_lock:
        if _centroids is not None:
            return _centroids
        # Try disk cache first
        try:
            f = _cache_file()
            if f.is_file():
                _centroids = json.loads(f.read_text(encoding="utf-8"))
                logger.info("intent_classifier: loaded %d centroids from cache",
                            len(_centroids or {}))
                return _centroids
        except Exception:
            logger.debug("intent_classifier cache read failed", exc_info=True)
        # Build fresh
        all_texts: List[str] = []
        for intent in sorted(_CORPUS):
            all_texts.extend(_CORPUS[intent])
        embeds = _embed(all_texts)
        if embeds is None:
            return None
        # Slice back per intent and compute centroid
        out: Dict[str, List[float]] = {}
        idx = 0
        for intent in sorted(_CORPUS):
            n = len(_CORPUS[intent])
            out[intent] = _mean(embeds[idx:idx + n])
            idx += n
        # Persist
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _cache_file().with_suffix(".tmp")
            tmp.write_text(json.dumps(out), encoding="utf-8")
            tmp.replace(_cache_file())
        except Exception:
            logger.debug("intent_classifier cache write failed", exc_info=True)
        _centroids = out
        logger.info("intent_classifier: built %d centroids fresh", len(out))
        return _centroids


def classify(text: str) -> Tuple[Optional[str], float]:
    """Return ``(intent, confidence)`` for an utterance.

    confidence is the cosine similarity to the best-matching centroid
    in [0, 1]. Returns ``(None, 0.0)`` when embeddings are unavailable
    or text is empty — caller should treat that as "no signal" and
    fall through to existing routing.
    """
    if not (text or "").strip():
        return None, 0.0
    centroids = _ensure_centroids()
    if not centroids:
        return None, 0.0
    embeds = _embed([text])
    if not embeds:
        return None, 0.0
    q = embeds[0]
    best_intent: Optional[str] = None
    best_score = 0.0
    for intent, centroid in centroids.items():
        score = _cosine(q, centroid)
        if score > best_score:
            best_score = score
            best_intent = intent
    return best_intent, best_score


def hint_for(text: str, threshold: float = _THRESHOLD) -> str:
    """Return a system-prompt hint string when the utterance falls
    above the confidence threshold, else "".

    Designed to be spliced into the system prompt by the LLM caller:

        persona += "\\n\\n" + intent_classifier.hint_for(user_text)

    No-op when the classifier is unavailable so the caller stays
    unchanged in failure modes.
    """
    intent, score = classify(text)
    if intent is None:
        return ""
    if score < threshold:
        return ""
    hint = _HINTS.get(intent)
    if not hint:
        return ""
    # Tag with the score so we can grep the logs for steered turns
    return f"[INTENT={intent} score={score:.2f}] {hint}"


def warmup_async() -> None:
    """Build centroids on a background thread so the first turn doesn't
    pay the embedding cost. Idempotent."""
    threading.Thread(
        target=_ensure_centroids, daemon=True, name="intent-warmup",
    ).start()


__all__ = ["classify", "hint_for", "warmup_async"]
