#!/usr/bin/env python3
"""Validate the release identity and write a non-secret Tauri build overlay."""
import argparse
import base64
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = 'io.github.churntechnologies.bandpeek'

def validate(require_key=False, tag=None):
    config = json.loads((ROOT / 'src-tauri/tauri.conf.json').read_text())
    version = config['version']
    versions = [json.loads((ROOT / name).read_text())['version'] for name in ['package.json', 'package-lock.json']]
    versions += [json.loads((ROOT/'package-lock.json').read_text())['packages']['']['version']]
    cargo = (ROOT / 'src-tauri/Cargo.toml').read_text().split('[package]',1)[1].split('[',1)[0]
    versions += [re.search(r'^version = \"([^\"]+)\"', cargo, re.M).group(1),
                 re.search(r'name = \"bandpeek\"\nversion = \"([^\"]+)\"', (ROOT/'src-tauri/Cargo.lock').read_text()).group(1)]
    assert all(v == version for v in versions), 'Version mismatch across package/Cargo/Tauri metadata'
    assert tag is None or tag == f'v{version}', 'Tag must equal v + package version'
    assert re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version), 'Invalid release version'
    assert config['identifier'] == IDENTIFIER, 'Wrong application identifier'
    key = config['plugins']['updater']['pubkey'].strip()
    if require_key:
        assert key, 'Owner must embed a genuine updater public key before release'
    if key: validate_public_key(key)
    assert not any(config['plugins']['updater'].get(k, False) for k in
                   ['dangerousInsecureTransportProtocol', 'dangerousAcceptInvalidCerts', 'dangerousAcceptInvalidHostnames']), 'Updater verification/HTTPS must stay enabled'
    return config

def validate_public_key(key):
    decoded = base64.b64decode(key, validate=True).decode()
    assert decoded.startswith('untrusted comment:'), 'Invalid updater public key'
    raw = base64.b64decode(decoded.splitlines()[1], validate=True)
    assert len(raw) == 42 and raw[:2] in (b'Ed', b'ED'), 'Invalid updater public key payload'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--require-key', action='store_true')
    ap.add_argument('--tag')
    ap.add_argument('--overlay', type=Path)
    ap.add_argument('--trusted', action='store_true')
    args = ap.parse_args()
    config = validate(args.require_key, args.tag)
    if args.overlay:
        signed_updates = bool(os.environ.get('TAURI_SIGNING_PRIVATE_KEY', '').strip())
        if signed_updates:
            validate(True, args.tag)
        mac = {'signingIdentity': '-'}
        if args.trusted:
            required = ['APPLE_CERTIFICATE', 'APPLE_CERTIFICATE_PASSWORD', 'APPLE_SIGNING_IDENTITY',
                        'APPLE_ID', 'APPLE_PASSWORD', 'APPLE_TEAM_ID', 'TAURI_SIGNING_PRIVATE_KEY']
            assert all(os.environ.get(s, '').strip() for s in required), 'Trusted release credentials incomplete'
            identity = os.environ['APPLE_SIGNING_IDENTITY']
            assert identity.startswith('Developer ID Application:'), 'Requires Developer ID Application identity'
            mac['signingIdentity'] = identity
        args.overlay.parent.mkdir(parents=True, exist_ok=True)
        args.overlay.write_text(json.dumps({'bundle': {'createUpdaterArtifacts': signed_updates, 'macOS': mac}}, indent=2)+'\n')
    print(f"Release metadata consistent: {config['version']} / {IDENTIFIER}")

if __name__ == '__main__':
    main()
