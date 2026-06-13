import { useCallback, useEffect, useRef, useState } from 'react';
import QRCode from 'qrcode';
import { getBase } from '../lib/api';

/**
 * In-app WhatsApp pairing (Phase 8 #1). Renders the Baileys QR as a scannable
 * image, polls /whatsapp/pair/status, and offers a one-click enable that
 * writes the notify env vars. Desktop-relevant but works wherever the backend
 * is reachable.
 */

interface PairStatus {
  status: 'idle' | 'starting' | 'awaiting_scan' | 'connected' | 'error';
  qr?: string | null;
  jid?: string | null;
  reason?: string | null;
}

export function WhatsAppPairing() {
  const [status, setStatus] = useState<PairStatus>({ status: 'idle' });
  const [qrDataUrl, setQrDataUrl] = useState('');
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(`${getBase()}/whatsapp/pair/status`);
      const data: PairStatus = await res.json();
      setStatus(data);
      if (data.status === 'connected' || data.status === 'error') {
        if (pollRef.current) window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    } catch {
      /* transient */
    }
  }, []);

  useEffect(() => {
    if (status.qr) {
      QRCode.toDataURL(status.qr, { width: 240, margin: 1 }).then(setQrDataUrl).catch(() => {});
    } else {
      setQrDataUrl('');
    }
  }, [status.qr]);

  useEffect(() => () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
  }, []);

  const startPairing = useCallback(async () => {
    setBusy(true);
    setEnabled(false);
    try {
      await fetch(`${getBase()}/whatsapp/pair/start`, { method: 'POST' });
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(poll, 1500);
      void poll();
    } finally {
      setBusy(false);
    }
  }, [poll]);

  const enableNotifications = useCallback(async () => {
    setBusy(true);
    try {
      const res = await fetch(`${getBase()}/whatsapp/pair/enable`, { method: 'POST' });
      setEnabled(res.ok);
    } finally {
      setBusy(false);
    }
  }, []);

  const muted = { color: 'var(--color-text-tertiary)' };

  return (
    <div>
      <p className="text-sm mb-3" style={muted}>
        Pair a phone to receive watchdog alerts and the morning briefing on WhatsApp.
      </p>

      {(status.status === 'idle' || status.status === 'error') && (
        <button
          type="button"
          onClick={startPairing}
          disabled={busy}
          className="px-3 py-1.5 rounded-lg text-sm"
          style={{ background: 'var(--color-accent, #25D366)', color: '#fff', cursor: 'pointer' }}
        >
          {status.status === 'error' ? 'Retry pairing' : 'Pair device'}
        </button>
      )}

      {status.status === 'error' && status.reason && (
        <p className="text-xs mt-2" style={{ color: 'var(--color-error, #ff6b6b)' }}>{status.reason}</p>
      )}

      {(status.status === 'starting' || status.status === 'awaiting_scan') && (
        <div className="flex items-center gap-4 mt-2">
          {qrDataUrl ? (
            <img src={qrDataUrl} alt="WhatsApp pairing QR" width={200} height={200}
                 style={{ borderRadius: 8, background: '#fff', padding: 8 }} />
          ) : (
            <div style={{ width: 200, height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', ...muted }}>
              Generating QR…
            </div>
          )}
          <div className="text-sm" style={muted}>
            <div style={{ color: 'var(--color-text)' }}>On your phone:</div>
            WhatsApp → Settings → Linked Devices → Link a Device → scan this code.
          </div>
        </div>
      )}

      {status.status === 'connected' && (
        <div className="mt-2">
          <div className="text-sm" style={{ color: 'var(--color-success, #25D366)' }}>
            ✓ Paired{status.jid ? ` as ${status.jid}` : ''}.
          </div>
          {!enabled ? (
            <button
              type="button"
              onClick={enableNotifications}
              disabled={busy}
              className="px-3 py-1.5 rounded-lg text-sm mt-2"
              style={{ background: 'var(--color-accent, #25D366)', color: '#fff', cursor: 'pointer' }}
            >
              Enable WhatsApp notifications
            </button>
          ) : (
            <div className="text-sm mt-2" style={muted}>
              Notifications enabled — restart the stack to apply.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
