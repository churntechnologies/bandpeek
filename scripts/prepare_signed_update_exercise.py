#!/usr/bin/env python3
"""Prepare local updater fixtures; never read a private key or sign anything.

Run after building the current configured app. The owner signs both fixtures
interactively. These artifacts must never be published as a release.
"""
import gzip
import hashlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tarfile

from release_config import ROOT, IDENTIFIER, validate


def main():
    config = validate(require_key=True)
    bundle = ROOT / 'src-tauri/target/release/bundle/macos/BandPeek.app'
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    assert info['CFBundleIdentifier'] == IDENTIFIER
    assert info['CFBundleShortVersionString'] == config['version']
    assert info['CFBundleVersion'] == config['version']
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    out = ROOT / '.validation/signed-updater'
    out.mkdir(parents=True, exist_ok=True)
    archive = out / 'BandPeek.app.tar.gz'
    failure = out / 'install-failure.app.tar.gz'
    # Do not invalidate signatures already supplied by the owner on a rerun.
    for path in (archive, failure):
        assert not path.exists() and not Path(str(path) + '.sig').exists(), (
            f'Fixture already exists: {path}; preserve it or choose a fresh exercise'
        )
    # Match the official macOS updater layout: one top-level .app component.
    with tarfile.open(archive, 'w:gz', format=tarfile.USTAR_FORMAT) as tar:
        tar.add(bundle, arcname='BandPeek.app')
    with tarfile.open(archive, 'r:gz') as tar:
        binary = tar.extractfile('BandPeek.app/Contents/MacOS/bandpeek')
        assert binary is not None
        assert hashlib.sha256(binary.read()).digest() == hashlib.sha256(
            (bundle / 'Contents/MacOS/bandpeek').read_bytes()
        ).digest()
    # A signed, valid gzip stream with an invalid tar checksum passes genuine
    # signature verification, then fails in the official installer. This is
    # separate from changing bytes AFTER signing (download rejection).
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        entry = tarfile.TarInfo('BandPeek.app/validation-only')
        entry.size = 1
        tar.addfile(entry, io.BytesIO(b'x'))
    damaged = bytearray(buffer.getvalue())
    damaged[0] ^= 1
    failure.write_bytes(gzip.compress(damaged, mtime=0))
    try:
        with tarfile.open(failure, 'r:gz') as tar:
            tar.getmembers()
    except tarfile.ReadError:
        pass
    else:
        raise AssertionError('Installation-failure fixture unexpectedly parses')
    evidence = {
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'version': config['version'],
        'identifier': IDENTIFIER,
        'public_key_sha256': hashlib.sha256(config['plugins']['updater']['pubkey'].encode()).hexdigest(),
        'artifacts': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (archive, failure)},
        'signed': False,
        'distribution': 'LOCAL-VALIDATION-ONLY; never publish',
    }
    (out / 'preparation.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
