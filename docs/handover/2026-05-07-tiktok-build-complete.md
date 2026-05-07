# TikTok Business Module — Build Complete Handover
**Date:** 2026-05-07  
**Branch:** `jarvis/local-snapshot`  
**HEAD:** `e41ef4e`  
**Tests:** 40 passed (40/40 including 4 pre-existing warnings from torch/pynvml)

---

## What Was Built

A fully automated faceless TikTok business system integrated into J.A.R.V.I.S., targeting £5,000–£10,000/month revenue. All 11 implementation tasks are complete and committed.

### New Files

| File | Purpose |
|------|---------|
| `src/openjarvis/tiktok/__init__.py` | Module marker |
| `src/openjarvis/tiktok/state.py` | All JSON I/O — queue, posted, comments, settings, finance |
| `src/openjarvis/tiktok/trend_scorer.py` | Wraps HN + Reddit trends, scores 0–100 for TikTok virality |
| `src/openjarvis/tiktok/finance.py` | RPM-based revenue estimator (default £7.50/1k views) |
| `src/openjarvis/tiktok/video_gen.py` | Kling AI v1 client — JWT auth, submit, poll, download |
| `src/openjarvis/tiktok/tiktok_client.py` | TikTok Content Posting API v2 — OAuth, upload, stats, comments |
| `src/openjarvis/tiktok/pipeline.py` | Agent entry points + `get_pipeline_state()` |
| `jarvis_web/tiktok.html` | Full-page 6-tab dashboard at `/tiktok` |
| `tests/tiktok/` | 40 tests total across 8 test files |

### Modified Files

| File | Change |
|------|--------|
| `src/openjarvis/cli/brain_server.py` | 11 new `/tiktok/*` endpoints |
| `src/openjarvis/tools/agent_runner.py` | 6 TikTok agents in DEFAULT_AGENTS |
| `jarvis_web/brain.html` | TIKTOK button in Operations Center banner |

---

## Commit History (newest first)

```
e41ef4e  fix(tiktok): restore pointer-events on TIKTOK banner button via ob-chat-btn class
11dfaf3  fix(tiktok): XSS safety — escapeHtml/escapeAttr + event delegation for action buttons
e53613a  feat(tiktok): tiktok.html — full dashboard (6 tabs, live state polling)
4daf266  feat(tiktok): add TIKTOK button to Operations Center banner
0d4d56a  feat(tiktok): register 6 TikTok agents in agent_runner
203c919  feat(tiktok): brain_server routes — 11 new /tiktok/* endpoints
df34b69  fix(tiktok): tiktok_client — guard empty file, stream upload, handle HTTP errors
735ee8b  fix(tiktok): video_gen — duration as int, poll loop handles network errors
3cc66ae  feat(tiktok): pipeline orchestrator — state view + python_entry callables
c20db59  feat(tiktok): TikTok API client — OAuth, upload, stats, comments
170ec55  feat(tiktok): Kling AI client — submit, poll, download
c6ac21e  feat(tiktok): finance — RPM revenue estimator and monthly summary
7b1a05c  fix(tiktok): trend_scorer — guard None title, remove dead tag branch
bb367db  feat(tiktok): trend scorer — TikTok virality scoring 0-100
318f545  fix(tiktok): reject_video/reject_comment return False when ID not found
```

---

## Architecture Overview

```
Trend Miner (HN + Reddit)
    → trend_scorer.py (score 0–100)
    → state.py queue (pending approval)
        → Operator approves in /tiktok QUEUE tab
        → video_gen.py → Kling AI → MP4 download
        → tiktok_client.py → TikTok Direct Post upload
        → state.py posted list
            → stats_puller pulls views/likes/comments
            → finance.py estimates GBP earnings
            → comment_responder drafts replies
```

### State Storage

All state is JSON in `~/.openjarvis/tiktok/`:
- `queue.json` — pending/approved videos awaiting generation
- `posted.json` — published videos with stats
- `settings.json` — API keys, OAuth tokens, posting schedule
- `finance.json` — RPM, month-to-date totals
- `comments.json` — pending/approved comment replies

### 6 TikTok Agents (in agent_runner DEFAULT_AGENTS)

| Agent | Type | Entry |
|-------|------|-------|
| trend-scorer | claude | claude-sonnet-4-6 |
| script-writer-tiktok | claude | claude-sonnet-4-6 |
| video-generator | python | `openjarvis.tiktok.pipeline:video_generator_entry` |
| tiktok-publisher | python | `openjarvis.tiktok.pipeline:tiktok_publisher_entry` |
| stats-puller | python | `openjarvis.tiktok.pipeline:stats_puller_entry` |
| comment-responder | claude | claude-sonnet-4-6 |

### Dashboard Tabs

1. **PIPELINE** — agent status + trend virality scores
2. **APPROVAL QUEUE** — approve/reject pending videos
3. **POSTED** — all published videos with view/like counts
4. **FINANCE** — RPM settings, monthly GBP estimate
5. **COMMENTS** — approve/reject AI-drafted replies
6. **SETTINGS** — Kling API key, TikTok OAuth, posting schedule

---

## Known Issues / Open Items

### 1. TikTok Routes Are Unauthenticated
**File:** `src/openjarvis/cli/brain_server.py`  
**Detail:** The 11 `/tiktok/*` GET routes were added to `_dispatch_get_unauthenticated` (same as the health-check and public endpoints). For a local-only server this is fine, but if brain_server is ever exposed beyond localhost these should move to the authenticated dispatch alongside `/markets` routes.  
**Severity:** Low (local-only deployment, no external exposure currently)

### 2. Stray `find` Command — Source Unidentified
**Observed:** `find /e/Claude/OpenJarvis -name "*.py" -type f | xargs grep -l "tiktok\|TikTok" 2>/dev/null` kept running in the terminal during the session.  
**Investigated:**
- `claude_hook_post.ps1` — relays stdin to brain server, no `find` command
- `C:\Users\User\.claude\agent-flow\hook.js` — only mentions "find live extension instances" in a comment; no shell `find` invocation found
- Not a scheduled task we created  
**Hypothesis:** Likely triggered by a git hook, an IDE extension (VS Code), or a pre-commit hook scanning for new files. Could also be a subagent from earlier in the session that was invoked but whose process persisted.  
**Next step:** Check `E:\Claude\OpenJarvis\.git\hooks\` for any custom hooks. Also check if a VS Code extension (e.g., ESLint, Python language server) is scanning the workspace.

---

## Setup Required Before First Use

The module is fully built but needs real credentials to function end-to-end:

### Kling AI API Key
1. Sign up at [kling.ai](https://kling.ai) for API access
2. Open the TikTok dashboard at `http://localhost:7710/tiktok`
3. Go to **SETTINGS** tab
4. Enter `Kling API Key` and `Kling API Secret`
5. Click **Save Settings**

The video-generator agent reads these from `~/.openjarvis/tiktok/settings.json` via `state.get_setting("kling_api_key")`.

### TikTok OAuth
1. Create a TikTok Developer app at [developers.tiktok.com](https://developers.tiktok.com)
2. Enable **Content Posting API v2** scope
3. In SETTINGS tab, enter your `Client Key` and `Client Secret`
4. Click **Authorize TikTok** — this builds the OAuth URL
5. Complete the OAuth flow; the access token is stored automatically

---

## Testing

```bash
# Run all TikTok tests
.venv/Scripts/python.exe -m pytest tests/tiktok/ -v

# Run full suite to check for regressions
.venv/Scripts/python.exe -m pytest -q
```

All 40 tests pass as of HEAD `e41ef4e`.

---

## Next Potential Work Items

1. **Move TikTok routes to authenticated dispatch** (low priority, local-only)
2. **Scheduling** — wire up CronCreate to auto-trigger trend-scorer + video-generator on a daily schedule
3. **Video preview in queue** — the APPROVAL QUEUE tab shows video metadata but no inline preview; add an `<video>` element once download path is populated
4. **Comment analytics** — the COMMENTS tab could show reply engagement rate
5. **Multi-account support** — settings.json currently holds one set of TikTok credentials
