"""Branded readiness boot screen for the Jarvis desktop app.

When the backend isn't up yet, the window loads this self-contained page instead
of a broken Studio. It polls the pywebview JS API (``window.pywebview.api``) for
readiness, lets the user start the backend, and navigates to Studio the moment
the stack is healthy. Kept dependency-free (a single HTML string) so it works
both in dev and inside a PyInstaller bundle.
"""

from __future__ import annotations

import html

_BOOT_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; height: 100vh; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 28px;
    background: radial-gradient(120% 120% at 50% 0%, #0c1622 0%, #060a10 60%, #03060a 100%);
    color: #cfe8ff; font-family: 'Segoe UI', system-ui, sans-serif; user-select: none;
  }
  .brand { font-size: 30px; letter-spacing: 0.42em; font-weight: 600; color: #eaf6ff;
           text-shadow: 0 0 24px rgba(64,170,255,.45); }
  .sub { font-size: 12px; letter-spacing: 0.28em; text-transform: uppercase; color: #5f7c96; }
  .ring { width: 58px; height: 58px; border-radius: 50%;
          border: 3px solid rgba(64,170,255,.18); border-top-color: #40aaff;
          animation: spin 0.9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #status { font-size: 14px; color: #9fc4e6; min-height: 20px; }
  .svc { display: flex; gap: 14px; font-size: 12px; color: #7ea6c8; }
  .svc span::before { content: "•"; margin-right: 5px; color: #3a536b; }
  .svc span.up::before { color: #34d27b; }
  button { margin-top: 6px; padding: 11px 22px; border-radius: 999px; cursor: pointer;
           border: 1px solid rgba(64,170,255,.4); background: rgba(64,170,255,.10);
           color: #eaf6ff; font-size: 12px; letter-spacing: 0.16em; text-transform: uppercase; }
  button:hover { background: rgba(64,170,255,.22); }
  button:disabled { opacity: .5; cursor: default; }
  @media (prefers-reduced-motion: reduce) { .ring { animation: none; } }
</style>
</head>
<body>
  <div class="brand">__TITLE__</div>
  <div class="sub">Local-first agent workspace</div>
  <div class="ring" aria-hidden="true"></div>
  <div id="status">Waiting for the Jarvis backend…</div>
  <button id="startBtn" type="button">Start backend</button>
  <script>
    const studioUrl = "__STUDIO_URL__";
    const statusEl = document.getElementById('status');
    const startBtn = document.getElementById('startBtn');
    function api() { return (window.pywebview && window.pywebview.api) || null; }
    async function check() {
      const a = api();
      if (!a) { statusEl.textContent = 'Desktop API unavailable.'; return; }
      try {
        const ok = await a.ready();
        if (ok) { statusEl.textContent = 'Backend ready — opening Studio…'; window.location.replace(studioUrl); return; }
      } catch (e) { /* keep polling */ }
      setTimeout(check, 1500);
    }
    startBtn.addEventListener('click', async () => {
      const a = api(); if (!a) return;
      startBtn.disabled = true; statusEl.textContent = 'Starting backend stack…';
      try { await a.start_backend(); } catch (e) {}
      setTimeout(() => { startBtn.disabled = false; }, 4000);
    });
    window.addEventListener('pywebviewready', check);
    setTimeout(check, 800); // fallback if the ready event already fired
  </script>
</body>
</html>"""


def boot_html(studio_url: str, *, title: str = "J.A.R.V.I.S.") -> str:
    """Return the self-contained boot/readiness HTML page."""
    return (
        _BOOT_TEMPLATE
        .replace("__STUDIO_URL__", html.escape(studio_url, quote=True))
        .replace("__TITLE__", html.escape(title))
    )
