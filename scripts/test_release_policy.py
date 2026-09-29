"""Release policy regressions; fake secrets never sign or publish artifacts."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from release_config import APPLE_SECRETS, ROOT, UNNOTARIZED_BETA_TAG, signing_configuration, validate
from release_manifest import generate_assets


class SigningPolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = validate(True)
        # Keep this exception's policy tests valid after the production version advances.
        self.config['version'] = '0.1.0-beta.1'
        self.env = {'TAURI_SIGNING_PRIVATE_KEY': 'test-placeholder-not-a-key'}

    def select(self, tag=UNNOTARIZED_BETA_TAG, approval=UNNOTARIZED_BETA_TAG):
        return signing_configuration(self.config, tag, approval, self.env)

    def test_exact_approved_beta_is_ad_hoc_with_updater_signing(self):
        overlay, mode = self.select()
        self.assertEqual(mode, 'unnotarized-beta')
        self.assertEqual(overlay['bundle'], {'createUpdaterArtifacts': True, 'macOS': {'signingIdentity': '-'}})

    def test_missing_or_wrong_approval_and_branch_build_stay_local(self):
        for tag, approval in [(UNNOTARIZED_BETA_TAG, None), (UNNOTARIZED_BETA_TAG, 'v0.1.0-beta.2'),
                              (None, UNNOTARIZED_BETA_TAG)]:
            with self.subTest(tag=tag, approval=approval):
                self.assertEqual(self.select(tag, approval)[1], 'local-validation')

    def test_exception_does_not_expand_to_other_versions(self):
        for version in ['0.1.0-beta.2', '0.1.0']:
            self.config['version'] = version
            self.assertEqual(self.select(f'v{version}', f'v{version}')[1], 'local-validation')

    def test_wrong_tag_fails(self):
        with self.assertRaisesRegex(AssertionError, 'Tag must equal'):
            self.select('v0.1.0-beta.2')

    def test_missing_updater_secret_fails_in_all_apple_modes(self):
        for trusted in [False, True]:
            if trusted:
                self.env.update({name: 'test-placeholder' for name in APPLE_SECRETS})
                self.env['APPLE_SIGNING_IDENTITY'] = 'Developer ID Application: Test'
            self.env.pop('TAURI_SIGNING_PRIVATE_KEY', None)
            with self.assertRaisesRegex(AssertionError, 'Production updater signing key required'):
                self.select()

    def test_missing_public_key_fails(self):
        self.config['plugins']['updater']['pubkey'] = ''
        with self.assertRaises(AssertionError):
            self.select()

    def test_every_partial_apple_configuration_fails(self):
        for name in APPLE_SECRETS:
            with self.subTest(secret=name):
                env = dict(self.env, **{name: 'test-placeholder'})
                with self.assertRaisesRegex(AssertionError, 'Partial Apple credentials'):
                    signing_configuration(self.config, UNNOTARIZED_BETA_TAG, UNNOTARIZED_BETA_TAG, env)

    def test_complete_apple_credentials_preserve_developer_id_path(self):
        self.env.update({name: 'test-placeholder' for name in APPLE_SECRETS})
        self.env['APPLE_SIGNING_IDENTITY'] = 'Developer ID Application: Test'
        overlay, mode = self.select()
        self.assertEqual(mode, 'developer-id')
        self.assertEqual(overlay['bundle']['macOS']['signingIdentity'], self.env['APPLE_SIGNING_IDENTITY'])
        self.assertTrue(overlay['bundle']['createUpdaterArtifacts'])
        self.config['version'] = '0.2.0'
        self.assertEqual(self.select('v0.2.0', None)[1], 'developer-id')
        self.env['APPLE_SIGNING_IDENTITY'] = 'Apple Development: Test'
        with self.assertRaisesRegex(AssertionError, 'Developer ID Application'):
            self.select('v0.2.0', None)

    def test_insecure_updater_transport_is_rejected(self):
        config = copy.deepcopy(validate(True))
        config['plugins']['updater']['dangerousAcceptInvalidCerts'] = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Keep metadata intact and only change the proposed trust configuration.
            for name in ['package.json', 'package-lock.json', 'src-tauri/Cargo.toml', 'src-tauri/Cargo.lock']:
                dest = root/name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ROOT/name).read_bytes())
            (root/'src-tauri/tauri.conf.json').write_text(json.dumps(config))
            with patch('release_config.ROOT', root):
                with self.assertRaisesRegex(AssertionError, 'verification/HTTPS must stay enabled'):
                    validate(True)


class ManifestTests(unittest.TestCase):
    def test_beta_payload_checksums_and_public_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root/'bundle'
            (bundle/'macos').mkdir(parents=True)
            (bundle/'dmg').mkdir()
            # The workflow's mandatory Rust verifier handles cryptographic validation.
            # These fixtures test packaging only; no real candidate signature is claimed.
            (bundle/'macos/BandPeek.app.tar.gz').write_bytes(b'archive fixture')
            (bundle/'macos/BandPeek.app.tar.gz.sig').write_text('signature fixture')
            (bundle/'dmg/BandPeek.dmg').write_bytes(b'dmg fixture')
            output = root/'assets'
            with patch('release_manifest.validate', return_value={'version': '0.1.0-beta.1'}):
                generate_assets(bundle, UNNOTARIZED_BETA_TAG, output, 'unnotarized-beta')
            manifest = json.loads((output/'latest.json').read_text())
            self.assertEqual(manifest['version'], '0.1.0-beta.1')
            platform = manifest['platforms']['darwin-aarch64']
            self.assertEqual(platform['signature'], 'signature fixture')
            self.assertEqual(platform['url'], f'https://github.com/churntechnologies/bandpeek/releases/download/{UNNOTARIZED_BETA_TAG}/BandPeek.app.tar.gz')
            notes = (output/'RELEASE_NOTES.md').read_text()
            self.assertIn('not Apple Developer ID signed or notarized', notes)
            self.assertIn('System Settings → Privacy & Security → Open Anyway', notes)
            self.assertEqual(manifest['notes'].rstrip(), notes.rstrip())
            checksums = dict(line.split('  ', 1)[::-1] for line in (output/'SHA256SUMS').read_text().splitlines())
            self.assertEqual(set(checksums), {'BandPeek.app.tar.gz', 'BandPeek.app.tar.gz.sig', 'BandPeek.dmg', 'latest.json'})
            for name, digest in checksums.items():
                self.assertEqual(digest, hashlib.sha256((output/name).read_bytes()).hexdigest())
            with patch('release_manifest.validate', return_value={'version': '0.1.0-beta.1'}):
                with self.assertRaisesRegex(AssertionError, 'output directory must be empty'):
                    generate_assets(bundle, UNNOTARIZED_BETA_TAG, output, 'unnotarized-beta')
                with self.assertRaisesRegex(AssertionError, 'limited to the approved beta'):
                    generate_assets(bundle, 'v0.1.0-beta.2', root/'other-assets', 'unnotarized-beta')
            (bundle/'macos/BandPeek.app.tar.gz.sig').unlink()
            with patch('release_manifest.validate', return_value={'version': '0.1.0-beta.1'}):
                with self.assertRaisesRegex(AssertionError, 'Missing updater signature'):
                    generate_assets(bundle, UNNOTARIZED_BETA_TAG, output, 'unnotarized-beta')


if __name__ == '__main__':
    unittest.main()
