#!/usr/bin/env python3
"""README screenshots from a *demo* history database (never real usage).

Creates a temporary database with synthetic per-minute totals for today
(built-in Apple apps, a command-line tool and shared system helpers), runs the
release app on it, and captures the main window and popup in light and dark
appearance through the validation-only WebKit snapshot command. Live speeds in
the header come from the real collector.

Usage: python3 scripts/m5_screenshots.py
Output: docs/screenshots/{main,popup}-{light,dark}.png
"""

import os
import pathlib
import queue
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / 'src-tauri/target/release/bandpeek'
OUT = ROOT / 'docs' / 'screenshots'
MB = 1_000_000

# identity_key, stored name, bundle id, executable path, download, upload (bytes today)
DEMO = [
    ('bundle:com.apple.Safari', 'Safari', 'com.apple.Safari', '/Applications/Safari.app/Contents/MacOS/Safari', 1_214 * MB, 84 * MB),
    ('bundle:com.apple.AppStore', 'App Store', 'com.apple.AppStore', '/System/Applications/App Store.app/Contents/MacOS/App Store', 642 * MB, 4 * MB),
    ('bundle:com.apple.Music', 'Music', 'com.apple.Music', '/System/Applications/Music.app/Contents/MacOS/Music', 412 * MB, 6 * MB),
    ('bundle:com.apple.Photos', 'Photos', 'com.apple.Photos', '/System/Applications/Photos.app/Contents/MacOS/Photos', 38 * MB, 214 * MB),
    ('exec:/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.Networking.xpc/Contents/MacOS/com.apple.WebKit.Networking',
     'com.apple.WebKit.Networking', None,
     '/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.Networking.xpc/Contents/MacOS/com.apple.WebKit.Networking',
     188 * MB, 12 * MB),
    ('bundle:com.apple.mail', 'Mail', 'com.apple.mail', '/System/Applications/Mail.app/Contents/MacOS/Mail', 96 * MB, 31 * MB),
    ('exec:/usr/bin/curl', 'curl', None, '/usr/bin/curl', 52 * MB, 300_000),
    ('bundle:com.apple.MobileSMS', 'Messages', 'com.apple.MobileSMS', '/System/Applications/Messages.app/Contents/MacOS/Messages', 14 * MB, 9 * MB),
    ('bundle:com.apple.Maps', 'Maps', 'com.apple.Maps', '/System/Applications/Maps.app/Contents/MacOS/Maps', 11 * MB, 1 * MB),
    ('exec:/usr/sbin/mDNSResponder', 'mDNSResponder', None, '/usr/sbin/mDNSResponder', 3_100_000, 1_200_000),
    ('exec:/usr/libexec/nsurlsessiond', 'nsurlsessiond', None, '/usr/libexec/nsurlsessiond', 2_400_000, 400_000),
]

SCHEMA = """
CREATE TABLE apps (id INTEGER PRIMARY KEY AUTOINCREMENT, identity_key TEXT NOT NULL UNIQUE,
  application_name TEXT NOT NULL, bundle_id TEXT, icon_path TEXT, executable_path TEXT,
  first_seen_utc INTEGER NOT NULL, last_seen_utc INTEGER NOT NULL);
CREATE INDEX idx_apps_identity ON apps(identity_key);
CREATE TABLE traffic_buckets (bucket_start_utc INTEGER NOT NULL, app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
  download_bytes INTEGER NOT NULL, upload_bytes INTEGER NOT NULL, PRIMARY KEY (bucket_start_utc, app_id)) WITHOUT ROWID;
CREATE INDEX idx_buckets_time ON traffic_buckets(bucket_start_utc);
CREATE TABLE monitoring_gaps (id INTEGER PRIMARY KEY AUTOINCREMENT, start_utc INTEGER NOT NULL, end_utc INTEGER NOT NULL,
  generation INTEGER NOT NULL, reason TEXT NOT NULL);
CREATE INDEX idx_gaps_start ON monitoring_gaps(start_utc);
PRAGMA user_version = 1;
"""


def demo_db(path):
    now = int(time.time())
    midnight = int(time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1)))
    minutes = max(1, min(180, (now - midnight) // 60 - 1))  # spread over the last few hours of today
    start = now - minutes * 60
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        for key, name, bundle, exe, down, up in DEMO:
            cur = db.execute('INSERT INTO apps (identity_key, application_name, bundle_id, executable_path, first_seen_utc, last_seen_utc)'
                             ' VALUES (?,?,?,?,?,?)', (key, name, bundle, exe, start, now))
            for m in range(minutes):
                bucket = (start // 60 + m) * 60
                # Last minute takes the remainder so totals are exact.
                d = down // minutes + (down % minutes if m == minutes - 1 else 0)
                u = up // minutes + (up % minutes if m == minutes - 1 else 0)
                db.execute('INSERT INTO traffic_buckets VALUES (?,?,?,?)', (bucket, cur.lastrowid, d, u))


class App:
    def __init__(self, env, outdir):
        self.lines = queue.Queue()
        self.proc = subprocess.Popen([str(APP), '--validation-mode', 'visible', '--validation-no-record', '--validation-dir', str(outdir)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, env=env, cwd=ROOT)
        threading.Thread(target=lambda: [self.lines.put(l.strip()) for l in self.proc.stdout], daemon=True).start()

    def send(self, command, wait=0.0):
        self.proc.stdin.write(command + '\n')
        self.proc.stdin.flush()
        time.sleep(wait)

    def wait_for(self, prefix, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line.startswith(prefix):
                return line[len(prefix):]
        return None


def crop_top(src, dst, height):
    """Keeps the top `height` pixel rows of an 8-bit PNG (WebKit snapshots end
    with a blank band as high as the title bar). Pure Python: `sips` crops centred."""
    import struct
    import zlib
    data = pathlib.Path(src).read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos, chunks = 8, []
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        chunks.append((kind, data[pos + 8:pos + 8 + length]))
        pos += 12 + length
    ihdr = dict(chunks)[b'IHDR']
    width, _, depth, color, _, _, interlace = struct.unpack('>IIBBBBB', ihdr)
    assert depth == 8 and interlace == 0 and color in (2, 6)
    bpp = 4 if color == 6 else 3
    raw = zlib.decompress(b''.join(c for k, c in chunks if k == b'IDAT'))
    stride = width * bpp
    rows, prev = [], bytearray(stride)
    for y in range(height):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(b'\x00' + bytes(line))
        prev = line

    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0)
    pathlib.Path(dst).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
                                  + chunk(b'IDAT', zlib.compress(b''.join(rows), 9)) + chunk(b'IEND', b''))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix='bandpeek-shots-'))
    demo_db(work / 'demo.db')
    env = dict(os.environ, BANDPEEK_DB_PATH=str(work / 'demo.db'), BANDPEEK_SETTINGS_PATH=str(work / 'settings.json'))
    app = App(env, work)
    try:
        app.wait_for('validation-state=')
        app.send('resize 840 600', 1)
        app.send('front', 6)  # on screen (pages skip polling while hidden); icons and a live frame arrive
        for look in ('dark', 'light'):
            app.send(f'appearance {look}', 2)
            app.send('front', 1)
            app.send("eval main window.__TAURI_INTERNALS__.invoke('validation_report',{text:'scrollY='+scrollY+' header='+document.querySelector('.header').getBoundingClientRect().top+' vp='+innerWidth+'x'+innerHeight})", 1)
            print(look, app.wait_for('validation-report=', 3))
            app.send(f'snapshot {look}', 2)
            # A harness-launched app can't take activation, so the popup may
            # blur-close before the capture; retry until the capture exists.
            for _ in range(8):
                app.send('popup-close', 0.5)
                app.send('popup', 3)
                app.send(f'snapshot {look}-popup', 2)
                if (work / f'{look}-popup-tray.png').exists():
                    break
            app.send('popup-close', 1)
        for look in ('dark', 'light'):
            crop_top(work / f'{look}-main.png', OUT / f'main-{look}.png', 1200)  # 600 pt at 2x
            shutil.copy(work / f'{look}-popup-tray.png', OUT / f'popup-{look}.png')
    finally:
        app.send('quit')
        try:
            app.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            app.proc.kill()
        shutil.rmtree(work, ignore_errors=True)
    for f in sorted(OUT.glob('*.png')):
        print(f.relative_to(ROOT), subprocess.check_output(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', str(f)], text=True).split()[-3:])


if __name__ == '__main__':
    main()
