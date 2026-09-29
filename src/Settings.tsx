import { useEffect, useRef, useState } from 'react';
import { api, type LoginItem, type Settings } from './api';
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
const MENU_BAR: [Settings['menu_bar_display'], string][] = [
  ['speeds_only', 'Speeds only'],
  ['icon_and_speeds', 'Icon + speeds'],
  ['icon_only', 'Icon only'],
];
const RETENTION = [30, 90, 180, 365];
const LOGIN: ['off' | 'on', string][] = [
  ['off', 'Off'],
  ['on', 'On'],
];

function loginHelp(item: LoginItem | null): string {
  switch (item?.state) {
    case 'requires_approval':
      return 'Waiting for approval in System Settings → General → Login Items.';
    case 'unavailable':
      return 'Available when BandPeek runs as an installed app.';
    default:
      return 'Starts BandPeek in the menu bar when you log in, without opening this window.';
  }
}

function Choice<T extends string | number>({
  label,
  options,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  options: [T, string][];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(([key, text]) => (
        <button
          key={String(key)}
          className="segment"
          aria-pressed={value === key}
          disabled={disabled}
          onClick={() => onChange(key)}
        >
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
  const [login, setLogin] = useState<LoginItem | null>(null);
  const sheet = useRef<HTMLDivElement>(null);

  // The system owns this state (it can also change in System Settings), so
  // read it whenever the sheet opens or the window regains focus.
  useEffect(() => {
    const refresh = () => void api.loginItem().then(setLogin).catch(() => setLogin(null));
    refresh();
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, []);

  const setLaunchAtLogin = (on: boolean) =>
    api
      .setLoginItem(on)
      .then((item) => {
        setLogin(item);
        setError(item.error ? `Launch at login: ${item.error}` : '');
      })
      .catch((e) => setError(String(e)));

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
          <span>Menu bar display</span>
          <Choice label="Menu bar display" options={MENU_BAR} value={settings.menu_bar_display}
            onChange={(menu_bar_display) => save({ menu_bar_display })} />
        </div>
        <div className="setting setting-stacked" data-login-state={login?.state ?? 'loading'}>
          <div className="setting-row">
            <span>Open at login</span>
            <Choice
              label="Open at login"
              options={LOGIN}
              value={login && login.state !== 'disabled' && login.state !== 'unavailable' ? 'on' : 'off'}
              disabled={!login || login.state === 'unavailable'}
              onChange={(v) => void setLaunchAtLogin(v === 'on')}
            />
          </div>
          <div className="setting-help">
            {loginHelp(login)}
            {login?.state === 'requires_approval' && (
              <>
                {' '}
                <button className="link" onClick={() => void api.openLoginItems()}>
                  Open Login Items…
                </button>
              </>
            )}
          </div>
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
          <p>
            BandPeek shows a best-effort estimate of the TCP/UDP traffic each app’s sockets sent and received, on all
            network interfaces. It is not an ISP, billing or wire-level meter, and its totals will not match your
            provider’s.
          </p>
          <p>
            Local and LAN traffic is included. Very short-lived processes can be missed. System helpers such as WebKit
            Networking carry traffic for other apps and are listed on their own. Samples are taken every 2 seconds and
            saved about once a minute, so an abrupt crash can lose up to a minute of recent activity.
          </p>
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
