import { useEffect, useRef, useState } from 'react';
import { api, type Settings } from './api';
import { retentionLabel } from './format';

const APPEARANCE: [Settings['appearance'], string][] = [
  ['system', 'System'],
  ['light', 'Light'],
  ['dark', 'Dark'],
];
const UNITS: [Settings['units'], string][] = [
  ['decimal', 'GB'],
  ['binary', 'GiB'],
];
const RETENTION = [30, 90, 180, 365];

function Choice<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: [T, string][];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(([key, text]) => (
        <button key={String(key)} className="segment" aria-pressed={value === key} onClick={() => onChange(key)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function SettingsSheet({ settings, onClose }: { settings: Settings; onClose: () => void }) {
  const [error, setError] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [cleared, setCleared] = useState(false);
  const sheet = useRef<HTMLDivElement>(null);

  useEffect(() => {
    sheet.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = (patch: Partial<Settings>) =>
    api
      .setSettings({ ...settings, ...patch })
      .then(() => setError(''))
      .catch((e) => setError(String(e)));

  const clear = async () => {
    try {
      await api.clearHistory();
      setConfirming(false);
      setCleared(true);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" ref={sheet} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <div className="sheet-title" id="settings-title">
          Settings
        </div>
        <div className="setting">
          <span>Appearance</span>
          <Choice label="Appearance" options={APPEARANCE} value={settings.appearance} onChange={(appearance) => save({ appearance })} />
        </div>
        <div className="setting">
          <span>Units</span>
          <Choice label="Units" options={UNITS} value={settings.units} onChange={(units) => save({ units })} />
        </div>
        <div className="setting setting-stacked">
          <div className="setting-row">
            <span>History retention</span>
            <Choice
              label="History retention"
              options={RETENTION.map((d) => [d, retentionLabel(d)] as [number, string])}
              value={settings.retention_days}
              onChange={(retention_days) => save({ retention_days })}
            />
          </div>
          <div className="setting-help">History older than this is deleted automatically.</div>
        </div>
        <div className="setting setting-stacked">
          <div className="setting-row">
            <span>Clear history</span>
            {confirming ? (
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="button" onClick={() => setConfirming(false)}>
                  Cancel
                </button>
                <button className="button danger" onClick={clear}>
                  Clear History
                </button>
              </div>
            ) : (
              <button
                className="button"
                onClick={() => {
                  setCleared(false);
                  setConfirming(true);
                }}
              >
                Clear History…
              </button>
            )}
          </div>
          <div className="setting-help" role="status">
            {cleared
              ? 'History cleared.'
              : confirming
                ? 'Delete all stored history? This cannot be undone.'
                : 'Deletes all usage stored on this device. Tracking continues.'}
          </div>
        </div>
        {error && (
          <div className="setting danger" role="alert">
            {error}
          </div>
        )}
        <div className="note">
          BandPeek records observed TCP/UDP traffic per app on all network interfaces, including local traffic. It is
          not an ISP or billing meter, and very short-lived activity can be missed. Samples are taken every 5 seconds and
          written to disk about once a minute, so a crash can lose the most recent unsaved minute.
        </div>
        <div className="sheet-footer">
          <button className="primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
