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
APPLE_SECRETS = ['APPLE_CERTIFICATE', 'APPLE_CERTIFICATE_PASSWORD', 'APPLE_SIGNING_IDENTITY',
                 'APPLE_ID', 'APPLE_PASSWORD', 'APPLE_TEAM_ID']

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


def eligible_unnotarized_beta(version, tag, approved_unnotarized_beta_tag):
    """Allow only an exactly approved vMAJOR.MINOR.PATCH-beta.N release.

    Numeric identifiers follow SemVer: ASCII digits and no leading zeroes.
    The caller must obtain the tag from a real tag ref, never a branch name.
    """
    number = r'(?:0|[1-9][0-9]*)'
    return (re.fullmatch(rf'{number}\.{number}\.{number}-beta\.{number}', version) is not None
            and tag == f'v{version}'
            and approved_unnotarized_beta_tag == tag)


def signing_configuration(config, tag, approved_unnotarized_beta_tag, env):
    """Select Apple trust separately from mandatory production updater signing."""
    validate_public_key(config['plugins']['updater']['pubkey'].strip())
    assert env.get('TAURI_SIGNING_PRIVATE_KEY', '').strip(), 'Production updater signing key required for every candidate'
    assert tag is None or tag == f"v{config['version']}", 'Tag must equal v + package version'
    present = [bool(env.get(name, '').strip()) for name in APPLE_SECRETS]
    assert all(present) or not any(present), 'Partial Apple credentials: complete or remove them before building'
    mac = {'signingIdentity': '-'}
    mode = 'local-validation'
    if all(present):
        identity = env['APPLE_SIGNING_IDENTITY']
        assert identity.startswith('Developer ID Application:'), 'Requires Developer ID Application identity'
        mac['signingIdentity'] = identity
        mode = 'developer-id'
    elif eligible_unnotarized_beta(config['version'], tag, approved_unnotarized_beta_tag):
        mode = 'unnotarized-beta'
    return {'bundle': {'createUpdaterArtifacts': True, 'macOS': mac}}, mode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--require-key', action='store_true')
    ap.add_argument('--tag')
    ap.add_argument('--overlay', type=Path)
    ap.add_argument('--trusted', action='store_true')
    ap.add_argument('--approved-unnotarized-beta-tag')
    ap.add_argument('--github-output', type=Path)
    args = ap.parse_args()
    config = validate(args.require_key, args.tag)
    if args.overlay:
        overlay, mode = signing_configuration(config, args.tag, args.approved_unnotarized_beta_tag, os.environ)
        if args.trusted:
            assert mode == 'developer-id', 'Trusted release credentials incomplete'
        args.overlay.parent.mkdir(parents=True, exist_ok=True)
        args.overlay.write_text(json.dumps(overlay, indent=2)+'\n')
        if args.github_output:
            with args.github_output.open('a') as output:
                output.write(f'mode={mode}\n')
        print(f'Candidate signing mode: {mode}; updater signature verification required')
    print(f"Release metadata consistent: {config['version']} / {IDENTIFIER}")

if __name__ == '__main__':
    main()
