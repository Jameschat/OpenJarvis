"""TikTok business automation subsystem for J.A.R.V.I.S.

Modules:
  state        — JSON state I/O (queue, posted, finance, comments, settings)
  trend_scorer — TikTok virality scoring wrapping trend_miner
  finance      — RPM-based revenue estimation
  video_gen    — Kling AI text-to-video client
  tiktok_client — TikTok Content Posting API + stats + comments
  pipeline     — Orchestrator + python_entry callables for in-process agents
"""
