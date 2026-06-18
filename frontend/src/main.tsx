import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { ErrorBoundary } from './components/ErrorBoundary';
import App from './App';
import { initApiBase } from './lib/api';
import '@fontsource-variable/geist';
import '@fontsource/chakra-petch/500.css';
import '@fontsource/chakra-petch/600.css';
import '@fontsource/chakra-petch/700.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import '@fontsource/ibm-plex-mono/600.css';
import './index.css';

function applyTheme() {
  try {
    const raw = localStorage.getItem('openjarvis-settings');
    const settings = raw ? JSON.parse(raw) : {};
    const theme = settings.theme || 'system';
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else if (theme === 'light') {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
    }
  } catch { /* use system default */ }
}

applyTheme();

// Last-resort visibility: if anything fails OUTSIDE React (uncaught error,
// unhandled promise, chunk-load failure), the window would otherwise go blank
// white. Paint the actual error onto #root so it is diagnosable in the packaged
// desktop app (which has no devtools).
function paintFatal(label: string, detail: string) {
  try {
    const root = document.getElementById('root');
    if (!root) return;
    root.innerHTML =
      '<div style="padding:20px;font:12px/1.5 ui-monospace,Consolas,monospace;' +
      'color:#ffb4b4;background:#141416;height:100vh;overflow:auto;white-space:pre-wrap">' +
      '<strong>JARVIS UI ERROR — ' +
      label.replace(/[<&]/g, '_') +
      '</strong>\n\n' +
      String(detail).replace(/</g, '&lt;') +
      '</div>';
  } catch {
    /* nothing more we can do */
  }
}

window.addEventListener('error', (e) => {
  const err = (e as ErrorEvent).error;
  // Resource (script/style) load failures have no .error; report the target src.
  const target = e.target as { src?: string; href?: string } | null;
  const detail = err
    ? err.stack || err.message
    : 'Failed to load resource: ' + (target?.src || target?.href || (e as ErrorEvent).message || 'unknown');
  paintFatal('uncaught error', detail);
});

window.addEventListener('unhandledrejection', (e) => {
  const r = (e as PromiseRejectionEvent).reason;
  paintFatal('unhandled rejection', (r && (r.stack || r.message)) || String(r));
});

function redirectTauriToStudio() {
  if (
    typeof window !== 'undefined' &&
    window.__TAURI_INTERNALS__ &&
    window.location.pathname === '/'
  ) {
    window.history.replaceState(null, '', '/studio');
  }
}

redirectTauriToStudio();

// Fetch the API base URL from the Tauri backend before rendering.
// This ensures JARVIS_PORT is defined in one place (the Rust backend).
// In non-Tauri environments this is a no-op.
initApiBase().finally(() => {
  try {
    createRoot(document.getElementById('root')!).render(
      <StrictMode>
        <ErrorBoundary>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </ErrorBoundary>
      </StrictMode>,
    );
  } catch (err) {
    paintFatal('render failed', (err instanceof Error && (err.stack || err.message)) || String(err));
  }
});
