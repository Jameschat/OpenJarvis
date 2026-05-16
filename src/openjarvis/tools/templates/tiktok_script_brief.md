# TikTok script brief — AI build-in-public niche

This is the brief the `script-writer` agent receives whenever a new short-form
video is requested. It produces output the `producer` agent can directly turn
into an MP4.

## Audience

Tech-curious 18–34. They scroll TikTok looking for "wait, what?" moments.
They stop scrolling for visible AI doing something genuinely surprising.

## Niche

AI build-in-public — specifically: personal AI agents, multi-agent teams,
agent-to-vault memory, voice assistants, automation, "I built X with Claude/Codex".

## Format constraints

- **Length:** 30–60 seconds. Bias toward 45s — long enough for payoff, short
  enough to loop.
- **Aspect:** 9:16 (1080×1920).
- **Hook window:** seconds 0–2. Must stop the scroll. No setup, no preamble.
- **Reveal window:** seconds 3–35. Live demo or screen recording is the visual.
- **Loop ending:** last 3 seconds either ask a question or tease a follow-up
  that loops back to the hook so re-watches happen.

## Script output schema

The agent MUST output a single markdown file with this exact structure
(frontmatter + sections):

```
---
hook_score: <0–10 self-rating>
length_target: <e.g. 45s>
target_platform: tiktok-primary
source_topic: <link to trend note or topic snippet>
status: draft
tags: [content, tiktok, draft]
---

# <video working title>

## NARRATION (literal, line-by-line)

[0:00] <Hook line — under 8 words ideally>
[0:03] <Setup line>
[0:08] <Demo callout>
…each line stamped with timecode and intended length…

## ON-SCREEN TEXT (per timecode)

[0:00] HOOK TEXT (large, top-third, max 6 words)
[0:08] DEMO LABEL (mid, max 4 words)
…

## B-ROLL / VISUAL CUES (per timecode)

[0:00] Mission Control galaxy spinning, backend-dev star pulsing
[0:05] Cut to terminal: prompt + response visible
[0:18] Phone notification of Jarvis voice reply
…

## CAPTIONS

(Verbatim transcript of NARRATION, ready for auto-captioning)

## LOOP HOOK

(How the last second connects back to the first second so re-watches feel natural)
```

## Forbidden patterns (will get demonetised in 2026)

- Generic stock B-roll behind AI-narrated text-to-speech with no real demo
- "5 ways AI will change the world" templated lists
- Pure ChatGPT screenshot videos with no original work
- Music + slideshow with no narration

## Rewarded patterns

- Real screen recording of YOUR system actually working
- Counter-intuitive results ("I expected X, got Y")
- Show-don't-tell: the demo IS the value, narration is glue
- Specific numbers ("£20/month, 10 agents, 0 employees")
- Loop endings that re-bait the hook
