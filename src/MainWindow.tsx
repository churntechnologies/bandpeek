import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  onEvent,
  usePolling,
  useLive,
  useSettings,
  type AppIdentity,
  type AppRate,
  type HistoryRange,
  type HistoryView,
  type Settings,
  type SortKey,
  type UiState,
  type ViewRange,
} from './api';
import { AppIcon, Amount, Mark, SplitBar, iconFor, useIcons } from './components';
import { appDetail, fmt, fmtText, matchesQuery, periodLabel, retentionLabel, sharePct, unitsLabel } from './format';
import { SettingsSheet } from './Settings';

const RANGES: [ViewRange, string][] = [
  ['now', 'Now'],
  ['today', 'Today'],
  ['yesterday', 'Yesterday'],
  ['last_7_days', 'Last 7 Days'],
  ['last_30_days', 'Last 30 Days'],
];
const SORTS: [SortKey, string][] = [
  ['download', 'Download'],
  ['upload', 'Upload'],
  ['total', 'Total'],
];
const SORT_FIELD: Record<SortKey, 'download_bytes' | 'upload_bytes' | 'total_bytes'> = {
  download: 'download_bytes',
  upload: 'upload_bytes',
  total: 'total_bytes',
};
const totalRate = (r: AppRate) => r.download_bytes_per_second + r.upload_bytes_per_second;
const RATE_FIELD: Record<SortKey, (r: AppRate) => number> = {
  download: (r) => r.download_bytes_per_second,
  upload: (r) => r.upload_bytes_per_second,
  total: totalRate,
};
const NO_APPS: AppRate[] = [];
// Minute-bucketed history: refreshing more often than this adds nothing visible.
const HISTORY_REFRESH_MS = 15_000;
const PAST_RANGE_REFRESH_MS = 60_000;

export function MainWindow() {
  const settings = useSettings();
  const [ui, setUi] = useState<UiState | null>(null);

  useEffect(() => {
    api.uiState().then(setUi);
  }, []);

  // Persist view state natively so it survives WebView destruction.
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const update = useCallback((patch: Partial<UiState>) => {
    setUi((prev) => {
      if (!prev) return prev;
      const next = { ...prev, ...patch };
      clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => void api.setUiState(next), 'query' in patch ? 300 : 0);
      return next;
    });
  }, []);

  const [settingsOpen, setSettingsOpen] = useState(false);

  if (!settings || !ui) return <div className="window" />;
  return (
    <div className="window">
      <Header units={settings.units} />
      <div className="toolbar">
        <div className="segmented" role="group" aria-label="Time range">
          {RANGES.map(([key, label]) => (
            <button
              key={key}
              className="segment"
              aria-pressed={ui.range === key}
              onClick={() => update({ range: key })}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <input
          className="filter"
          type="search"
          placeholder="Filter apps"
          aria-label="Filter apps"
          spellCheck={false}
          value={ui.query}
          onChange={(e) => update({ query: e.target.value })}
          onKeyDown={(e) => e.key === 'Escape' && update({ query: '' })}
        />
        <button className="button" onClick={() => setSettingsOpen(true)}>
          Settings
        </button>
      </div>
      {ui.range === 'now' ? (
        <LiveApps sort={ui.sort} query={ui.query} settings={settings} onSort={(sort) => update({ sort })} />
      ) : (
        <History range={ui.range} sort={ui.sort} query={ui.query} settings={settings} onSort={(sort) => update({ sort })} />
      )}
      <StatusBar settings={settings} />
      {settingsOpen && <SettingsSheet settings={settings} onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}

/** Only this subtree re-renders on each live-rate refresh. */
function Header({ units }: { units: Settings['units'] }) {
  const live = useLive();
  const down = fmt(live?.download_bytes_per_second ?? 0, units);
  const up = fmt(live?.upload_bytes_per_second ?? 0, units);
  return (
    <header className="header">
      <div className="brand">
        <Mark />
        <span className="brand-name">BandPeek</span>
      </div>
      <div className="spacer" />
      <div className="speeds" aria-label="Current speed">
        <div className="speed" title="Current download speed">
          <span className="arrow-down">↓</span>
          <span className="speed-value">{down.n}</span>
          <span className="unit">{down.u}/s</span>
        </div>
        <div className="speed" title="Current upload speed">
          <span className="arrow-up">↑</span>
          <span className="speed-value">{up.n}</span>
          <span className="unit">{up.u}/s</span>
        </div>
      </div>
    </header>
  );
}

function History({
  range,
  sort,
  query,
  settings,
  onSort,
}: {
  range: HistoryRange;
  sort: SortKey;
  query: string;
  settings: Settings;
  onSort: (sort: SortKey) => void;
}) {
  const [view, setView] = useState<HistoryView | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const next = await api.history(range);
      setView(next);
      setError('');
    } catch (e) {
      setError(String(e));
    }
  }, [range]);

  // Show the new range immediately rather than the previous range's numbers.
  useEffect(() => setView((v) => (v?.range === range ? v : null)), [range]);
  usePolling(load, range === 'yesterday' ? PAST_RANGE_REFRESH_MS : HISTORY_REFRESH_MS, [load]);
  useEffect(() => onEvent('history-changed', () => void load()), [load]);

  const current = view?.range === range ? view : null;
  const all = useMemo(() => current?.rows.filter((r) => r.total_bytes > 0) ?? [], [current]);
  const sorted = useMemo(() => {
    const field = SORT_FIELD[sort];
    return [...all].sort((a, b) => b[field] - a[field] || b.total_bytes - a.total_bytes || a.display_name.localeCompare(b.display_name));
  }, [all, sort]);
  const max = useMemo(() => all.reduce((m, r) => Math.max(m, r.total_bytes), 0), [all]);
  const needle = query.trim().toLowerCase();
  const rows = useMemo(
    () => (needle ? sorted.filter((r) => matchesQuery(r, needle)) : sorted),
    [sorted, needle],
  );
  useIcons(all); // re-renders as icons arrive, so iconFor() below picks them up

  const units = settings.units;
  const summary = current?.summary ?? { download_bytes: 0, upload_bytes: 0, total_bytes: 0 };

  return (
    <>
      <Summary
        label="Totals for the selected range"
        stats={[
          ['Downloaded', summary.download_bytes],
          ['Uploaded', summary.upload_bytes],
          ['Total', summary.total_bytes],
        ]}
        units={units}
        period={current ? `${periodLabel(current)} · ${appCount(all.length)}` : ''}
      />
      <div className="table" role="table" aria-label="Applications" aria-rowcount={rows.length + 1}>
        <TableHead sort={sort} onSort={onSort} />
        <div className="tbody" role="rowgroup">
          {rows.map((row) => (
            <Row
              key={row.identity_key}
              app={row}
              down={row.download_bytes}
              up={row.upload_bytes}
              share={row.share_percent}
              max={max}
              units={units}
              icon={iconFor(row.identity_key)}
            />
          ))}
          {current && all.length > 0 && rows.length === 0 && <div className="empty">No apps match “{query.trim()}”</div>}
          {current && all.length === 0 && (
            <div className="empty">{error ? `Could not read history: ${error}` : 'No network activity recorded for this period yet.'}</div>
          )}
          {!current && error && <div className="empty">Could not read history: {error}</div>}
        </div>
      </div>
    </>
  );
}

/** Per-app speeds from the latest sample. Re-renders with each live update. */
function LiveApps({
  sort,
  query,
  settings,
  onSort,
}: {
  sort: SortKey;
  query: string;
  settings: Settings;
  onSort: (sort: SortKey) => void;
}) {
  const live = useLive();
  const all = live?.apps ?? NO_APPS;
  const sorted = useMemo(() => {
    const value = RATE_FIELD[sort];
    return [...all].sort((a, b) => value(b) - value(a) || totalRate(b) - totalRate(a) || a.display_name.localeCompare(b.display_name));
  }, [all, sort]);
  const max = useMemo(() => all.reduce((m, r) => Math.max(m, totalRate(r)), 0), [all]);
  const needle = query.trim().toLowerCase();
  const rows = useMemo(
    () => (needle ? sorted.filter((r) => matchesQuery(r, needle)) : sorted),
    [sorted, needle],
  );
  useIcons(all);

  const units = settings.units;
  const down = live?.download_bytes_per_second ?? 0;
  const up = live?.upload_bytes_per_second ?? 0;
  // Share of the whole machine's current speed, not just of the listed apps.
  const total = down + up;
  const state = live?.state ?? 'starting';
  const idle =
    state === 'tracking'
      ? 'Nothing is using the network right now.'
      : state === 'gap'
        ? 'Collector restarting; speeds return after the next sample.'
        : state === 'stopped'
          ? 'Not tracking.'
          : 'Measuring… speeds appear after the first two samples.';

  return (
    <>
      <Summary
        label="Current speeds"
        stats={[
          ['Download', down],
          ['Upload', up],
          ['Total', total],
        ]}
        units={units}
        suffix="/s"
        period={`Right now · ${appCount(all.length)}`}
      />
      <div className="table" role="table" aria-label="Applications using the network now" aria-rowcount={rows.length + 1}>
        <TableHead sort={sort} onSort={onSort} />
        <div className="tbody" role="rowgroup">
          {rows.map((row) => (
            <Row
              key={row.identity_key}
              app={row}
              down={row.download_bytes_per_second}
              up={row.upload_bytes_per_second}
              share={total > 0 ? (totalRate(row) / total) * 100 : 0}
              max={max}
              units={units}
              suffix="/s"
              icon={iconFor(row.identity_key)}
            />
          ))}
          {all.length > 0 && rows.length === 0 && <div className="empty">No apps match “{query.trim()}”</div>}
          {all.length === 0 && <div className="empty">{idle}</div>}
        </div>
      </div>
    </>
  );
}

const appCount = (n: number) => `${n} ${n === 1 ? 'app' : 'apps'}`;

function Summary({
  label,
  stats,
  units,
  suffix = '',
  period,
}: {
  label: string;
  stats: [string, number][];
  units: Settings['units'];
  suffix?: string;
  period: string;
}) {
  const [down, , total] = stats.map(([, value]) => value);
  const ratio = total > 0 ? (down / total) * 100 : 0;
  return (
    <section className="summary" aria-label={label}>
      <div className="stats">
        {stats.map(([name, value]) => (
          <div className="stat" key={name}>
            <span className="stat-label">{name}</span>
            <Amount className="stat-value" value={fmt(value, units)} suffix={suffix} />
          </div>
        ))}
        <div className="spacer" />
        <span className="period">{period}</span>
      </div>
      <div className="split" aria-hidden="true">
        {total > 0 && (
          <>
            <div className="split-down" style={{ width: `${ratio}%` }} />
            <div className="split-up" />
          </>
        )}
      </div>
    </section>
  );
}

function TableHead({ sort, onSort }: { sort: SortKey; onSort: (sort: SortKey) => void }) {
  return (
    <div className="cols thead" role="row">
      <span className="th-left" role="columnheader">
        Application
      </span>
      {SORTS.map(([key, label]) => (
        <span key={key} className="th" role="columnheader" aria-sort={sort === key ? 'descending' : 'none'}>
          <button className="sort" data-active={sort === key} onClick={() => onSort(key)} title={`Sort by ${label.toLowerCase()}`}>
            {sort === key ? '↓ ' : ''}
            {label}
          </button>
        </span>
      ))}
      <span className="th" role="columnheader">
        Share
      </span>
    </div>
  );
}

/** One application: bytes for a history range, or bytes per second with `suffix="/s"`. */
const Row = memo(function Row({
  app,
  down,
  up,
  share,
  max,
  units,
  suffix = '',
  icon,
}: {
  app: AppIdentity;
  down: number;
  up: number;
  share: number;
  max: number;
  units: Settings['units'];
  suffix?: string;
  icon: string | null | undefined;
}) {
  return (
    <div className="cols row" role="row">
      <div className="app" role="cell" title={appDetail(app)}>
        <AppIcon src={app.kind === 'application' ? icon : null} size={18} />
        <span className="app-name">{app.display_name}</span>
      </div>
      <span className="num" role="cell">
        {fmtText(down, units) + suffix}
      </span>
      <span className="num" role="cell">
        {fmtText(up, units) + suffix}
      </span>
      <span className="num-strong" role="cell">
        {fmtText(down + up, units) + suffix}
      </span>
      <div className="share" role="cell">
        <SplitBar down={down} up={up} max={max} />
        <span className="pct">{sharePct(share)}</span>
      </div>
    </div>
  );
});

function StatusBar({ settings }: { settings: Settings }) {
  const state = useLive()?.state ?? 'starting';
  const label =
    state === 'tracking'
      ? 'Tracking'
      : state === 'gap'
        ? 'Collector restarting'
        : state === 'stopped'
          ? 'Not tracking'
          : 'Starting';
  return (
    <footer className="statusbar">
      <div className="tracking" role="status">
        <div className="dot" data-state={state} />
        {label} · Stored on this device only
      </div>
      <div className="spacer" />
      <span>
        History: {retentionLabel(settings.retention_days)} · {unitsLabel(settings.units)}
      </span>
    </footer>
  );
}
