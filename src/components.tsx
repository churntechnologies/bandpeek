import { memo, useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import type { AppHistoryRow } from './api';
import type { Formatted } from './format';

/** Final 4a open-lens mark (brand/bandpeek-mark-*.svg), tinted by theme tokens. */
export function Mark({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" style={{ display: 'block' }}>
      <path d="M9.18 4.82 A4.5 4.5 0 1 0 9.18 11.18" fill="none" stroke="var(--down)" strokeWidth="2.2" />
      <rect x="6" y="7" width="9.5" height="2" rx="1" fill="var(--up)" />
    </svg>
  );
}

/** Value with a smaller muted unit, as used in headers, summary and the tray. */
export function Amount({ value, suffix = '', className }: { value: Formatted; suffix?: string; className?: string }) {
  return (
    <span className={className}>
      {value.n} <span className="unit">{value.u + suffix}</span>
    </span>
  );
}

export function SplitBar({ down, up, max, className }: { down: number; up: number; max: number; className?: string }) {
  const pct = (v: number) => `${max > 0 ? (v / max) * 100 : 0}%`;
  return (
    <div className={`bar ${className ?? ''}`} aria-hidden="true">
      <div className="bar-down" style={{ width: pct(down) }} />
      <div className="bar-up" style={{ width: pct(up) }} />
    </div>
  );
}

/* ---- Application icons: resolved natively once, cached for the app lifetime. ---- */

const iconCache = new Map<string, string | null>();
const pending = new Set<string>();
const iconListeners = new Set<() => void>();

async function requestIcons(rows: AppHistoryRow[]) {
  const wanted = rows.filter((r) => !iconCache.has(r.identity_key) && !pending.has(r.identity_key));
  if (!wanted.length) return;
  wanted.forEach((r) => pending.add(r.identity_key));
  try {
    const icons = await invoke<Record<string, string | null>>('app_icons', {
      requests: wanted.map(({ identity_key, bundle_id, icon_path, executable_path }) => ({
        identity_key,
        bundle_id,
        icon_path,
        executable_path,
      })),
    });
    for (const [key, url] of Object.entries(icons)) iconCache.set(key, url);
  } catch {
    wanted.forEach((r) => iconCache.set(r.identity_key, null));
  } finally {
    wanted.forEach((r) => pending.delete(r.identity_key));
    iconListeners.forEach((l) => l());
  }
}

/** Loads icons for the given rows; returns a version number that changes as icons arrive. */
export function useIcons(rows: AppHistoryRow[]): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const listener = () => setVersion((v) => v + 1);
    iconListeners.add(listener);
    return () => void iconListeners.delete(listener);
  }, []);
  useEffect(() => {
    void requestIcons(rows);
  }, [rows]);
  return version;
}

export const iconFor = (identityKey: string) => iconCache.get(identityKey);

/**
 * Real bundle icon, or a neutral terminal glyph for CLI tools and background processes.
 * `src`: data URL, `null` (no bundle icon) or `undefined` (still loading: empty tile).
 */
export const AppIcon = memo(function AppIcon({ src, size }: { src: string | null | undefined; size: number }) {
  if (src) return <img className="icon" src={src} width={size} height={size} alt="" draggable={false} />;
  const radius = size >= 18 ? 5 : 4;
  return (
    <span className="icon-fallback" style={{ width: size, height: size, borderRadius: radius }} aria-hidden="true">
      {src === null && (
        <svg width={size * 0.62} height={size * 0.62} viewBox="0 0 10 10">
          <path d="M2 3 L4.2 5 L2 7" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M5.2 7.2 H8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
      )}
    </span>
  );
});
