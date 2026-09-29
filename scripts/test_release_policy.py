"""Release policy regressions; fake secrets never sign or publish artifacts."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from release_config import APPLE_SECRETS, ROOT, signing_configuration, validate
from release_manifest import generate_assets


class SigningPolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = validate(True)
        # Keep policy fixtures valid after the production version advances.
        self.config['version'] = '0.1.0-beta.2'
        self.env = {'TAURI_SIGNING_PRIVATE_KEY': 'test-placeholder-not-a-key'}

    def select(self, tag='v0.1.0-beta.2', approval='v0.1.0-beta.2'):
        return signing_configuration(self.config, tag, approval, self.env)

    def test_exact_approved_beta_is_ad_hoc_with_updater_signing(self):
        for version in ['0.1.0-beta.1', '0.1.0-beta.2', '0.1.0-beta.3', '1.2.3-beta.10']:
            with self.subTest(version=version):
                self.config['version'] = version
                overlay, mode = self.select(f'v{version}', f'v{version}')
                self.assertEqual(mode, 'unnotarized-beta')
                self.assertEqual(overlay['bundle'], {'createUpdaterArtifacts': True, 'macOS': {'signingIdentity': '-'}})

    def test_empty_apple_secrets_select_unnotarized_beta(self):
        # GitHub resolves absent secrets to empty strings in the credential-check step.
        # Only that step may receive them; the build must omit the variables entirely.
        self.env.update({name: '' for name in APPLE_SECRETS})
        self.assertEqual(self.select()[1], 'unnotarized-beta')

    def test_missing_or_wrong_approval_and_branch_build_stay_local(self):
        for tag, approval in [('v0.1.0-beta.2', None), ('v0.1.0-beta.2', ''),
                              ('v0.1.0-beta.2', 'v0.1.0-beta.1'),
                              ('v0.1.0-beta.2', 'v0.1.0-beta.3'),
                              ('v0.1.0-beta.2', 'v0.1.0-beta.2 '),
                              (None, 'v0.1.0-beta.2'), (None, None)]:
            with self.subTest(tag=tag, approval=approval):
                self.assertEqual(self.select(tag, approval)[1], 'local-validation')

    def test_stable_non_beta_and_invalid_versions_stay_local_even_when_approved(self):
        for version in ['0.1.0', '0.1.0-alpha.1', '0.1.0-rc.1', '0.1.0-dev',
                        '0.1.0-notbeta.2', '0.1.0-beta', '0.1.0-beta.02',
                        '01.1.0-beta.2', '0.01.0-beta.2', '0.1.00-beta.2',
                        '0.1.0-beta.-2', '0.1.0-beta.2.extra', '0.1.0-beta.2\n',
                        '０.1.0-beta.2', 'arbitrary']:
            with self.subTest(version=version):
                self.config['version'] = version
                self.assertEqual(self.select(f'v{version}', f'v{version}')[1], 'local-validation')

    def test_wrong_tag_fails(self):
        for tag in ['v0.1.0-beta.1', 'v0.1.0-beta.3', 'v0.1.0', 'arbitrary', '0.1.0-beta.2']:
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(AssertionError, 'Tag must equal'):
                    self.select(tag, tag)
        with self.assertRaisesRegex(AssertionError, 'Tag must equal'):
            validate(True, 'arbitrary')

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
                    signing_configuration(self.config, 'v0.1.0-beta.2', 'v0.1.0-beta.2', env)

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


class WorkflowSigningPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ruby/Psych ships with macOS (including the CI runners); no Python YAML
        # dependency is needed. Parse YAML instead of inspecting text snippets.
        cls.workflow = json.loads(subprocess.check_output([
            'ruby', '-rjson', '-ryaml', '-e',
            'puts JSON.generate(YAML.load_file(ARGV.fetch(0)))',
            str(ROOT / '.github/workflows/release.yml'),
        ], text=True))

    def setUp(self):
        self.build = self.workflow['jobs']['build']
        self.steps = self.build['steps']
        self.builds = [step for step in self.steps if step.get('uses', '').startswith('tauri-apps/tauri-action@')]

    def assert_no_apple_env(self, workflow, step):
        # Check all scopes: overriding an inherited variable with '' still exposes it.
        for scope in [workflow, workflow['jobs']['build'], step]:
            self.assertFalse(set(scope.get('env', {})) & set(APPLE_SECRETS),
                             'Unnotarized builds must omit Apple variables, even empty ones')

    def test_two_exclusive_build_paths_keep_updater_signing_and_dmg(self):
        self.assertEqual(len(self.builds), 2)
        trusted, unnotarized = self.builds
        self.assertEqual(trusted['if'], "steps.credentials.outputs.mode == 'developer-id'")
        self.assertEqual(unnotarized['if'],
                         "steps.credentials.outputs.mode == 'unnotarized-beta' || steps.credentials.outputs.mode == 'local-validation'")
        self.assertEqual(trusted['with'], unnotarized['with'])
        self.assertEqual(trusted['with'], {
            'args': '--target ${{ matrix.target }} --config .validation/release-config.json --bundles app,dmg',
        })
        for step in self.builds:
            for name in ['TAURI_SIGNING_PRIVATE_KEY', 'TAURI_SIGNING_PRIVATE_KEY_PASSWORD']:
                self.assertEqual(step['env'][name], '${{ secrets.' + name + ' }}')

    def test_unnotarized_build_omits_apple_env_at_every_scope(self):
        self.assert_no_apple_env(self.workflow, self.builds[1])
        self.assertEqual(set(self.builds[1]['env']),
                         {'TAURI_SIGNING_PRIVATE_KEY', 'TAURI_SIGNING_PRIVATE_KEY_PASSWORD'})

    def test_empty_apple_env_is_rejected_at_every_scope(self):
        # Prove that the guard rejects the original regression for all six names,
        # including workflow/job inheritance, rather than accepting falsy values.
        for name in APPLE_SECRETS:
            for level in ['workflow', 'job', 'step']:
                with self.subTest(variable=name, scope=level):
                    workflow = copy.deepcopy(self.workflow)
                    step = copy.deepcopy(self.builds[1])
                    scope = {'workflow': workflow, 'job': workflow['jobs']['build'], 'step': step}[level]
                    scope.setdefault('env', {})[name] = ''
                    with self.assertRaisesRegex(AssertionError, 'must omit Apple variables'):
                        self.assert_no_apple_env(workflow, step)

    def test_developer_id_build_receives_complete_apple_credentials(self):
        for name in APPLE_SECRETS:
            self.assertEqual(self.builds[0]['env'][name], '${{ secrets.' + name + ' }}')
        credentials = next(step for step in self.steps if step.get('id') == 'credentials')
        self.assertLess(self.steps.index(credentials), self.steps.index(self.builds[0]))
        for name in APPLE_SECRETS:
            self.assertEqual(credentials['env'][name], '${{ secrets.' + name + ' }}')
        self.assertIn('scripts/release_config.py', credentials['run'])
        self.assertIn('--github-output "$GITHUB_OUTPUT"', credentials['run'])
        self.assertEqual(credentials['env']['RELEASE_TAG'],
                         "${{ github.ref_type == 'tag' && github.ref_name || '' }}")
        self.assertEqual(credentials['env']['UNNOTARIZED_BETA_APPROVED_TAG'],
                         '${{ vars.UNNOTARIZED_BETA_APPROVED_TAG }}')
        self.assertEqual(self.build['outputs']['mode'], '${{ steps.credentials.outputs.mode }}')
        verification = next(step for step in self.steps if step.get('name') == 'Verify trusted bundle')
        self.assertEqual(verification['if'], self.builds[0]['if'])
        self.assertIn('codesign --verify --deep --strict', verification['run'])
        self.assertIn('xcrun stapler validate "$APP"', verification['run'])
        self.assertIn('bundle/dmg/*.dmg', verification['run'])
        self.assertIn('spctl --assess', verification['run'])

    def test_signature_verification_precedes_manifest_and_upload(self):
        verifier = next(step for step in self.steps
                        if step.get('name') == 'Verify updater signature against the embedded public key')
        self.assertNotIn('if', verifier)
        for argument in ['--bin bandpeek-verify-update', 'src-tauri/tauri.conf.json',
                         'BandPeek.app.tar.gz', 'BandPeek.app.tar.gz.sig']:
            self.assertIn(argument, verifier['run'])
        manifest = next(step for step in self.steps if step.get('name') == 'Generate signed updater manifest')
        self.assertEqual(manifest['if'], "github.ref_type == 'tag'")
        self.assertEqual(manifest['env']['UNNOTARIZED_BETA_APPROVED_TAG'],
                         '${{ vars.UNNOTARIZED_BETA_APPROVED_TAG }}')
        self.assertIn('--approved-unnotarized-beta-tag "$UNNOTARIZED_BETA_APPROVED_TAG"', manifest['run'])
        self.assertLess(self.steps.index(verifier), self.steps.index(manifest))
        for step in self.steps:
            if step.get('uses', '').startswith('actions/upload-artifact@'):
                self.assertLess(self.steps.index(manifest), self.steps.index(step))

    def test_publication_retains_exact_tag_approval_and_checksums(self):
        publish = self.workflow['jobs']['publish']
        self.assertEqual(publish['needs'], 'build')
        self.assertEqual(publish['environment'], 'public-release')
        self.assertEqual(' '.join(publish['if'].split()),
                         "github.ref_type == 'tag' && vars.PUBLIC_RELEASE_APPROVED_TAG == github.ref_name && "
                         "(needs.build.outputs.mode == 'developer-id' || "
                         "(needs.build.outputs.mode == 'unnotarized-beta' && "
                         "vars.UNNOTARIZED_BETA_APPROVED_TAG == github.ref_name))")
        publisher = publish['steps'][-1]['run']
        self.assertLess(publisher.index('sha256sum --check SHA256SUMS'), publisher.index('gh release create'))
        self.assertIn('*.dmg *.app.tar.gz *.app.tar.gz.sig latest.json SHA256SUMS', publisher)
        self.assertIn('--verify-tag', publisher)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bundle = self.root/'bundle'
        (self.bundle/'macos').mkdir(parents=True)
        (self.bundle/'dmg').mkdir()
        # The workflow's mandatory Rust verifier handles cryptographic validation.
        # These fixtures test packaging only; no real candidate signature is claimed.
        (self.bundle/'macos/BandPeek.app.tar.gz').write_bytes(b'archive fixture')
        (self.bundle/'macos/BandPeek.app.tar.gz.sig').write_text('signature fixture')
        (self.bundle/'dmg/BandPeek.dmg').write_bytes(b'dmg fixture')

    def generate(self, version, output):
        with patch('release_manifest.validate', return_value={'version': version}):
            generate_assets(self.bundle, f'v{version}', output, 'unnotarized-beta', f'v{version}')

    def test_beta_payload_checksums_and_public_disclosure(self):
        for version in ['0.1.0-beta.1', '0.1.0-beta.2']:
            with self.subTest(version=version):
                self.check_payload(version)

    def check_payload(self, version):
        tag = f'v{version}'
        output = self.root/version
        self.generate(version, output)
        manifest = json.loads((output/'latest.json').read_text())
        self.assertEqual(manifest['version'], version)
        platform = manifest['platforms']['darwin-aarch64']
        self.assertEqual(platform['signature'], 'signature fixture')
        self.assertEqual(platform['url'], f'https://github.com/churntechnologies/bandpeek/releases/download/{tag}/BandPeek.app.tar.gz')
        expected_notes = (ROOT/f'docs/releases/{version}.md').read_text()
        self.assertEqual(manifest['notes'], expected_notes)
        notes = (output/'RELEASE_NOTES.md').read_text()
        self.assertEqual(notes, expected_notes.rstrip()+'\n')
        if version == '0.1.0-beta.1':
            self.assertIn('not Apple Developer ID signed or notarized', notes)
            self.assertIn('System Settings → Privacy & Security → Open Anyway', notes)
        checksums = dict(line.split('  ', 1)[::-1] for line in (output/'SHA256SUMS').read_text().splitlines())
        self.assertEqual(set(checksums), {'BandPeek.app.tar.gz', 'BandPeek.app.tar.gz.sig', 'BandPeek.dmg', 'latest.json'})
        for name, digest in checksums.items():
            self.assertEqual(digest, hashlib.sha256((output/name).read_bytes()).hexdigest())
        with self.assertRaisesRegex(AssertionError, 'output directory must be empty'):
            self.generate(version, output)

    def test_missing_updater_signature_fails(self):
        (self.bundle/'macos/BandPeek.app.tar.gz.sig').unlink()
        with self.assertRaisesRegex(AssertionError, 'Missing updater signature'):
            self.generate('0.1.0-beta.2', self.root/'assets')

    def test_missing_version_specific_notes_fails_before_writing_assets(self):
        with patch('release_manifest.ROOT', self.root):
            with self.assertRaises(FileNotFoundError) as error:
                self.generate('0.1.0-beta.3', self.root/'assets')
        self.assertEqual(Path(error.exception.filename), self.root/'docs/releases/0.1.0-beta.3.md')
        self.assertFalse((self.root/'assets').exists())

    def test_future_beta_manifest_uses_its_own_notes(self):
        notes = self.root/'docs/releases/0.1.0-beta.3.md'
        notes.parent.mkdir(parents=True)
        notes.write_text('Future beta release notes\n')
        with patch('release_manifest.ROOT', self.root):
            self.generate('0.1.0-beta.3', self.root/'assets')
        manifest = json.loads((self.root/'assets/latest.json').read_text())
        self.assertEqual(manifest['version'], '0.1.0-beta.3')
        self.assertEqual(manifest['notes'], notes.read_text())

    def test_unnotarized_manifest_rejects_ineligible_or_unapproved_release(self):
        cases = [('0.1.0-beta.2', 'v0.1.0-beta.2', None),
                 ('0.1.0-beta.2', 'v0.1.0-beta.2', 'v0.1.0-beta.1'),
                 ('0.1.0-beta.2', 'v0.1.0-beta.3', 'v0.1.0-beta.3'),
                 ('0.1.0-beta.2', None, None)]
        cases += [(version, f'v{version}', f'v{version}') for version in
                  ['0.1.0', '0.1.0-alpha.1', '0.1.0-rc.1', '0.1.0-beta.02', 'arbitrary']]
        for version, tag, approval in cases:
            with self.subTest(version=version, tag=tag, approval=approval):
                with patch('release_manifest.validate', return_value={'version': version}):
                    with self.assertRaisesRegex(AssertionError, 'exactly approved beta matching'):
                        generate_assets(self.bundle, tag, self.root/'assets', 'unnotarized-beta', approval)
                self.assertFalse((self.root/'assets').exists())

    def test_developer_id_manifest_needs_no_beta_approval_or_notes_file(self):
        with patch('release_manifest.validate', return_value={'version': '0.1.0'}):
            with patch('release_manifest.ROOT', self.root):
                generate_assets(self.bundle, 'v0.1.0', self.root/'assets', 'developer-id')
        manifest = json.loads((self.root/'assets/latest.json').read_text())
        self.assertEqual(manifest['version'], '0.1.0')
        self.assertIn('Developer ID signed and notarized', manifest['notes'])


if __name__ == '__main__':
    unittest.main()
