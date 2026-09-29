import type { HistoryView, Settings } from './api';

export type Formatted = { n: string; u: string };

const DECIMAL = ['B', 'KB', 'MB', 'GB', 'TB'];
const BINARY = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];

/** specs/components.md: smallest unit KB/KiB; ≥100 → 0 dp, ≥10 → 1 dp, else 2 dp. */
export function fmt(bytes: number, units: Settings['units']): Formatted {
  const binary = units === 'binary';
  const base = binary ? 1024 : 1000;
  const names = binary ? BINARY : DECIMAL;
  if (!(bytes > 0) || !Number.isFinite(bytes)) return { n: '0', u: names[1] };
  const i = Math.min(4, Math.max(1, Math.floor(Math.log(bytes) / Math.log(base))));
  const v = bytes / Math.pow(base, i);
  return { n: v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2), u: names[i] };
}

export const fmtText = (bytes: number, units: Settings['units']) => {
  const f = fmt(bytes, units);
  return `${f.n} ${f.u}`;
};

export function sharePct(percent: number): string {
  return percent < 1 ? '<1%' : `${Math.round(percent)}%`;
}

const day = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const weekday = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' });

/** "Today, Sep 28", "Sun, Sep 27", "Sep 22 – 28", "Aug 30 – Sep 28". */
export function periodLabel(view: HistoryView): string {
  const start = new Date(view.range_start_utc * 1000);
  // The range end is exclusive; the last included moment is one second earlier.
  const end = new Date((view.range_end_utc - 1) * 1000);
  switch (view.range) {
    case 'today':
      return `Today, ${day.format(start)}`;
    case 'yesterday':
      return weekday.format(start);
    default:
      return day.formatRange(start, end);
  }
}

export function retentionLabel(days: number): string {
  return days === 365 ? '1 year' : `${days} days`;
}

export function unitsLabel(units: Settings['units']): string {
  return units === 'binary' ? 'Binary units (GiB)' : 'Decimal units (GB)';
}
