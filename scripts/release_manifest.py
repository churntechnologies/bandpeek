#!/usr/bin/env python3
"""Generate the official Tauri static manifest from Apple Silicon release files."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
from release_config import validate

REPO = 'churntechnologies/bandpeek'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bundle', type=Path)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    config = validate(True, args.tag)
    archives = list(args.bundle.glob('macos/*.app.tar.gz'))
    assert len(archives) == 1, 'Expected one Apple Silicon updater archive'
    archive = archives[0]
    signature = archive.with_name(archive.name+'.sig')
    assert signature.is_file() and signature.stat().st_size > 0, 'Missing updater signature'
    manifest = {'version': config['version'], 'notes': 'BandPeek macOS Apple Silicon beta.',
                'pub_date': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z'),
                'platforms': {'darwin-aarch64': {'signature': signature.read_text().strip(),
                    'url': f'https://github.com/{REPO}/releases/download/{args.tag}/{archive.name}'}}}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'latest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    import shutil
    files = [archive, signature, *args.bundle.glob('dmg/*.dmg')]
    assert len(files) == 3, 'Expected one installer DMG'
    for file in files: shutil.copy2(file,args.output/file.name)
    files = sorted(p for p in args.output.iterdir() if p.name != 'SHA256SUMS')
    (args.output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in files))

if __name__ == '__main__': main()
