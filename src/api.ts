import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { useEffect, useState, useSyncExternalStore } from 'react';

export type HistoryRange = 'today' | 'yesterday' | 'last_7_days' | 'last_30_days';
export type SortKey = 'download' | 'upload' | 'total';
export type TrackingState = 'starting' | 'tracking' | 'gap' | 'stopped';

export type LiveRates = {
  download_bytes_per_second: number;
  upload_bytes_per_second: number;
  state: TrackingState;
  sample_sequence: number;
  collector_generation: number;
};

export type AppKind = 'application' | 'command_line_tool' | 'system_process' | 'shared_system_process' | 'process';

export type AppHistoryRow = {
  identity_key: string;
  /** Name stored with the identity; `display_name` is the label to show. */
  application_name: string;
  display_name: string;
  kind: AppKind;
  bundle_id: string | null;
  icon_path: string | null;
  executable_path: string | null;
  download_bytes: number;
  upload_bytes: number;
  total_bytes: number;
  share_percent: number;
};

export type HistoryView = {
  range: HistoryRange;
  range_start_utc: number;
  range_end_utc: number;
  summary: { download_bytes: number; upload_bytes: number; total_bytes: number };
  rows: AppHistoryRow[];
};

export type Settings = {
  appearance: 'system' | 'light' | 'dark';
  units: 'decimal' | 'binary';
  menu_bar_display: 'speeds_only' | 'icon_and_speeds' | 'icon_only';
  retention_days: number;
};

export type UiState = { range: HistoryRange; sort: SortKey; query: string };

export type LoginItem = {
  state: 'enabled' | 'disabled' | 'requires_approval' | 'unavailable';
  error: string | null;
};

export const LIVE_REFRESH_MS = 5000; // History presentation; live rates arrive as events.

export const api = {
  liveRates: () => invoke<LiveRates>('live_rates'),
  history: (range: HistoryRange) => invoke<HistoryView>('get_history', { range }),
  clearHistory: () => invoke<void>('clear_history'),
  settings: () => invoke<Settings>('get_settings'),
  setSettings: (settings: Settings) => invoke<Settings>('set_settings', { settings }),
  uiState: () => invoke<UiState>('get_ui_state'),
  setUiState: (state: UiState) => invoke<void>('set_ui_state', { state }),
  openMain: () => invoke<void>('open_main_window'),
  closePopup: () => invoke<void>('close_tray_popup'),
  popupReady: (height: number) => invoke<void>('tray_popup_ready', { height }),
  quit: () => invoke<void>('quit_app'),
  loginItem: () => invoke<LoginItem>('get_login_item'),
  setLoginItem: (enabled: boolean) => invoke<LoginItem>('set_login_item', { enabled }),
  openLoginItems: () => invoke<void>('open_login_items_settings'),
};

/** Settings, kept in sync across windows through the `settings-changed` event. */
export function useSettings(): Settings | null {
  const [settings, setSettings] = useState<Settings | null>(null);
  useEffect(() => {
    let alive = true;
    api.settings().then((s) => alive && setSettings(s));
    const off = listen<Settings>('settings-changed', (e) => setSettings(e.payload));
    return () => {
      alive = false;
      void off.then((f) => f());
    };
  }, []);
  useEffect(() => {
    if (!settings) return;
    const root = document.documentElement;
    if (settings.appearance === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', settings.appearance);
  }, [settings?.appearance]);
  return settings;
}

/**
 * Calls `load` now and every `intervalMs`, skipping the call while the page is
 * hidden (occluded/minimized window). The timer itself keeps running: WKWebView
 * does not reliably dispatch `visibilitychange` on occlusion changes, so a loop
 * that stopped while hidden could never restart. `visibilitychange`/`focus`
 * only make a returning window refresh sooner.
 */
function poller(load: () => unknown, intervalMs: number): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let alive = true;
  let running = false;
  const tick = async () => {
    clearTimeout(timer);
    if (!alive) return;
    if (!document.hidden && !running) {
      running = true;
      try {
        await load();
      } catch {
        /* Surface errors through the caller's own state; keep polling. */
      } finally {
        running = false;
      }
    }
    if (alive) timer = setTimeout(tick, intervalMs);
  };
  const refresh = () => {
    if (!document.hidden) void tick();
  };
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('focus', refresh);
  void tick();
  return () => {
    alive = false;
    clearTimeout(timer);
    document.removeEventListener('visibilitychange', refresh);
    window.removeEventListener('focus', refresh);
  };
}

export function usePolling(load: () => void | Promise<void>, intervalMs: number, deps: unknown[]) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => poller(load, intervalMs), deps);
}

/* One event subscription per page. No live-rate UI timer. */
let live: LiveRates | null = null;
const liveListeners = new Set<() => void>();
let stopLive: (() => void) | null = null;

function startLive() {
  let alive = true;
  const update = (rates: LiveRates) => {
    if (!alive) return;
    // Ignore a delayed initial request if a newer collector event already arrived.
    if (live && rates.sample_sequence < live.sample_sequence) return;
    live = rates;
    liveListeners.forEach((l) => l());
  };
  const refresh = () => void api.liveRates().then(update);
  const off = listen<LiveRates>('live-rates', (e) => update(e.payload));
  const visibility = listen<boolean>('popup-visibility', (e) => { if (e.payload) refresh(); });
  void off.then(() => refresh());
  window.addEventListener('focus', refresh);
  return () => {
    alive = false;
    void off.then((f) => f());
    void visibility.then((f) => f());
    window.removeEventListener('focus', refresh);
  };
}

// Must be a stable function: an inline subscribe makes React resubscribe on
// every render, which would restart the subscription on every update.
function subscribeLive(listener: () => void) {
  liveListeners.add(listener);
  if (!stopLive) stopLive = startLive();
  return () => {
    liveListeners.delete(listener);
    if (liveListeners.size === 0 && stopLive) {
      stopLive();
      stopLive = null;
    }
  };
}
const getLive = () => live;

export function useLive(): LiveRates | null {
  return useSyncExternalStore(subscribeLive, getLive);
}

export function onEvent(name: string, handler: () => void) {
  const off = listen(name, handler);
  return () => void off.then((f) => f());
}
