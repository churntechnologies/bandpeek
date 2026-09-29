import { listen } from '@tauri-apps/api/event';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, onEvent, useLive, useSettings, type HistoryView } from './api';
import { AppIcon, Amount, SplitBar, iconFor, useIcons } from './components';
import { appDetail, fmt, fmtText } from './format';

const TOP_APPS = 5;

export function TrayPopup() {
  const settings = useSettings();
  const live = useLive();
  const [today, setToday] = useState<HistoryView | null>(null);
  const [failed, setFailed] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setToday(await api.history('today'));
    } catch {
      setFailed(true);
    }
  }, []);
  // Initialize the retained view once; refresh history on open and live samples.
  const visible = useRef(false);
  useEffect(() => {
    void load();
    const off = listen<boolean>('popup-visibility', (e) => {
      visible.current = e.payload;
      if (e.payload) void load();
    });
    return () => void off.then((f) => f());
  }, [load]);
  useEffect(() => onEvent('live-rates', () => { if (visible.current) void load(); }), [load]);
  useEffect(() => onEvent('history-changed', () => void load()), [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && void api.closePopup();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const top = (today?.rows ?? []).filter((r) => r.total_bytes > 0).slice(0, TOP_APPS);
  useIcons(top);
  const ready = settings !== null && (today !== null || failed);

  // Size the native window to the content, then show it (no empty flash).
  useLayoutEffect(() => {
    if (ready && root.current) void api.popupReady(root.current.getBoundingClientRect().height);
  }, [ready, top.length]);

  if (!settings) return null;
  const units = settings.units;
  const down = fmt(live?.download_bytes_per_second ?? 0, units);
  const up = fmt(live?.upload_bytes_per_second ?? 0, units);
  const summary = today?.summary ?? { download_bytes: 0, upload_bytes: 0, total_bytes: 0 };
  const max = top[0]?.total_bytes ?? 0;

  return (
    <div className="popup" ref={root}>
      <div className="popup-speeds">
        <div className="stat">
          <span className="popup-speed-label">
            <span className="arrow-down">↓</span> Download
          </span>
          <Amount className="popup-speed-value" value={down} suffix="/s" />
        </div>
        <div className="stat">
          <span className="popup-speed-label">
            <span className="arrow-up">↑</span> Upload
          </span>
          <Amount className="popup-speed-value" value={up} suffix="/s" />
        </div>
      </div>
      <div className="popup-today">
        <span className="muted">Today</span>
        <span className="popup-today-total">{fmtText(summary.total_bytes, units)}</span>
        <div className="spacer" />
        <span className="muted">
          <span style={{ color: 'var(--down)' }}>↓</span> {fmtText(summary.download_bytes, units)}
        </span>
        <span className="muted">
          <span style={{ color: 'var(--up)' }}>↑</span> {fmtText(summary.upload_bytes, units)}
        </span>
      </div>
      <div className="popup-section">
        <span>Top apps today</span>
      </div>
      <div className="popup-apps">
        {top.map((row) => (
          <div className="popup-app" key={row.identity_key} title={appDetail(row)}>
            <div className="app">
              <AppIcon src={row.kind === 'application' ? iconFor(row.identity_key) : null} size={16} />
              <span className="app-name">{row.display_name}</span>
            </div>
            <SplitBar down={row.download_bytes} up={row.upload_bytes} max={max} />
            <span className="num">{fmtText(row.total_bytes, units)}</span>
          </div>
        ))}
        {today && top.length === 0 && <div className="popup-empty">No network activity recorded yet today.</div>}
        {failed && !today && <div className="popup-empty">Could not read history.</div>}
      </div>
      <div className="popup-footer">
        <button className="popup-open" onClick={() => void api.openMain()}>
          Open BandPeek
        </button>
        <button className="popup-quit" onClick={() => void api.quit()}>
          Quit
        </button>
      </div>
    </div>
  );
}
