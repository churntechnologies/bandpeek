#!/usr/bin/env python3
"""Generate the official Tauri static manifest from Apple Silicon release files."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
from release_config import ROOT, eligible_unnotarized_beta, validate

REPO = 'churntechnologies/bandpeek'

def generate_assets(bundle, tag, output, mode, approved_unnotarized_beta_tag=None):
    config = validate(True, tag)
    if mode == 'unnotarized-beta':
        assert eligible_unnotarized_beta(config['version'], tag, approved_unnotarized_beta_tag), 'Unnotarized publication requires an exactly approved beta matching the package version'
        notes = (ROOT/f"docs/releases/{config['version']}.md").read_text()
    elif mode == 'developer-id':
        notes = f"BandPeek {config['version']} for macOS 13+ on Apple Silicon. Developer ID signed and notarized. Updater signatures are verified against the permanent production public key."
    else:
        notes = 'LOCAL-VALIDATION-ONLY: ad-hoc signed, unnotarized candidate. Not approved for publication.'
    archives = list(bundle.glob('macos/*.app.tar.gz'))
    assert len(archives) == 1, 'Expected one Apple Silicon updater archive'
    archive = archives[0]
    signature = archive.with_name(archive.name+'.sig')
    assert signature.is_file() and signature.stat().st_size > 0, 'Missing updater signature'
    files = [archive, signature, *bundle.glob('dmg/*.dmg')]
    assert len(files) == 3, 'Expected one installer DMG'
    assert not output.exists() or not any(output.iterdir()), 'Release output directory must be empty'
    manifest = {'version': config['version'], 'notes': notes,
                'pub_date': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z'),
                'platforms': {'darwin-aarch64': {'signature': signature.read_text().strip(),
                    'url': f'https://github.com/{REPO}/releases/download/{tag}/{archive.name}'}}}
    output.mkdir(parents=True, exist_ok=True)
    (output/'latest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (output/'RELEASE_NOTES.md').write_text(notes.rstrip()+'\n')
    import shutil
    for file in files: shutil.copy2(file,output/file.name)
    # Hash exactly the public payload; never sweep unrelated/stale files into checksums.
    files = sorted([output/file.name for file in files] + [output/'latest.json'])
    (output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in files))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('bundle', type=Path)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--mode', choices=['developer-id', 'unnotarized-beta', 'local-validation'], required=True)
    ap.add_argument('--approved-unnotarized-beta-tag')
    args = ap.parse_args()
    generate_assets(args.bundle, args.tag, args.output, args.mode, args.approved_unnotarized_beta_tag)

if __name__ == '__main__': main()
