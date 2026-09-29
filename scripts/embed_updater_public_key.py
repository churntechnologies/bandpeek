#!/usr/bin/env python3
"""Read only the .pub file; embed its public value in Tauri's config."""
import argparse
import json
from pathlib import Path
from release_config import ROOT, validate_public_key

ap = argparse.ArgumentParser()
ap.add_argument('public_key', type=Path)
args = ap.parse_args()
assert args.public_key.name.endswith('.pub'), 'Pass the public .pub file, never the private key'
path = ROOT/'src-tauri/tauri.conf.json'
config = json.loads(path.read_text())
key = args.public_key.read_text().strip()
validate_public_key(key)
config['plugins']['updater']['pubkey'] = key
path.write_text(json.dumps(config, indent=2)+'\n')
print('Public updater key embedded. No private material read.')
