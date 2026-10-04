"""Canonical HTML is enforced by real files and meaningful refusal cases."""
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('html_documentation_guard', ROOT/'scripts/check_html_documentation.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def page(content: str = '') -> str:
    return ('<!doctype html><html lang="en"><head><title>Guide</title></head>'
            '<body><main><h1 id="guide">Guide</h1>' + content + '</main></body></html>')


class HtmlDocumentationTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(folder)
        self.manifest = {'mapping': {'README.md': 'README.html', 'docs/guide.md': 'docs/guide.html'}}
        self.write('README.html', page('<a href="docs/guide.html#guide">Guide</a>'))
        self.write('docs/guide.html', page('<a href="../README.html#guide">Overview</a>'))
        self.save_manifest()

    def write(self, path: str, content: str):
        target = self.root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')

    def save_manifest(self):
        self.write('docs/html-migration-map.json', json.dumps(self.manifest))

    def errors(self):
        return '\n'.join(guard.check(self.root))

    def pointer(self):
        self.manifest['compatibility_pointers'] = ['docs/guide.md']
        self.save_manifest()
        text = ('# Document moved to HTML\n\n<a id="guide"></a>\n'
                'The maintained document is [guide.html](guide.html). '
                'This file is a link-only compatibility entry point for existing references '
                'from the separately managed Homemade LLM project; do not add substantive documentation here.\n')
        self.write('docs/guide.md', text)
        return text

    def test_real_current_library_passes(self):
        self.assertEqual(guard.check(ROOT), [])

    def test_current_handoffs_do_not_restore_retired_attribution_paperwork(self):
        from director_orchestrator.documentation import read_document
        paths = ['README.html', 'CLAUDE.md', 'prompts/director-system.md',
                 '.github/pull_request_template.md', 'docs/team-operating-charter.html',
                 'docs/contribution-attribution-policy.html',
                 'docs/project-bootstrap-protocol.html',
                 'docs/templates/contribution-submission.html',
                 'docs/templates/project-bootstrap/README.html',
                 'docs/templates/project-bootstrap/project-state-report.html',
                 'docs/templates/project-bootstrap/review-ready.html',
                 'docs/templates/project-bootstrap/workstream-plan.html']
        for path in paths:
            with self.subTest(path=path):
                text = ' '.join(read_document(ROOT/path).split())
                self.assertIn('2026-10-01', text)
                for obsolete in ('trailers on every commit', 'Every commit carries',
                                 'PR sole author and shares:', 'Contributor shares:',
                                 'Reviewed share_record_sha256:',
                                 'Enclose human-readable project messages',
                                 'recompute its sole author and shares',
                                 'Format-aware contribution tags for human responses',
                                 'under the relaying agent\'s own contribution pair',
                                 'Records say "Sole author:',
                                 'share-record text, or artifact change',
                                 'Sole author: <assistant> (<model>)',
                                 'share_record_sha256: <reviewer-recorded'):
                    self.assertNotIn(obsolete, text)
        policy = read_document(ROOT/'docs/contribution-attribution-policy.html')
        self.assertIn('No contributor may merge that pull request', policy)
        self.assertIn('preserve that gate and report its concrete refusal', policy)
        for name in ('README.html', 'CLAUDE.md', 'prompts/director-system.md'):
            text = ' '.join(read_document(ROOT/name).split())
            self.assertIn('Git Author/Committer', text)
            self.assertIn('contributor/merger separation', text)
            self.assertIn('deterministic gate', text)
        template = read_document(ROOT/'.github/pull_request_template.md')
        self.assertNotIn('share_record_sha256', template)
        self.assertIn('reviewer/merger independence', template)
        for identity in ('base_sha', 'merge_base_sha', 'Head SHA:', 'Tree/artifact digest'):
            self.assertIn(identity, template)

    def test_bootstrap_metadata_preserves_identity_without_retired_procedures(self):
        profile = yaml.safe_load((ROOT/'docs/templates/project-bootstrap/project-profile.yaml').read_text(encoding='utf-8'))
        attribution = profile['contribution_attribution']
        self.assertEqual(attribution['owner_direction'], '2026-10-01')
        self.assertFalse(attribution['custom_attribution_procedures'])
        self.assertTrue(attribution['preserve_historical_records'])
        self.assertTrue(attribution['verify_actual_identity_and_independence'])
        self.assertTrue(attribution['preserve_deterministic_gate_inputs'])
        self.assertIn('normal Git Author/Committer', attribution['provenance'])
        self.assertEqual(attribution['authentication_or_authority_effect'], 'none')
        self.assertFalse({'commit_trailer', 'pr_header', 'assistance_required_when_assisted'} & attribution.keys())

    def test_tracked_source_and_config_consumers_cannot_keep_removed_document_paths(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True, capture_output=True, timeout=20)
        consumers = {
            'src/lane.py': '"""Read ``docs/guide.md#guide`` before changing the lane."""\n',
            'scripts/lane.ps1': '# See ../docs/guide.md for operator instructions.\n',
            'prompts/lane.md': 'Read [the guide](../docs/guide.md).\n',
            '.env.example': '# Guide: docs/guide.md\n',
            'docs/lane.yaml': 'operator_guide: docs/guide.md\n',
        }
        for name, source in consumers.items():
            self.write(name, source)
        subprocess.run(['git', 'add', '--', *consumers], cwd=self.root, check=True, capture_output=True, timeout=20)
        errors = self.errors()
        for name, source in consumers.items():
            self.assertIn(f'{name}:1: removed Markdown reference docs/guide.md; use docs/guide.html', errors)
            self.write(name, source.replace('guide.md', 'guide.html'))
        self.assertEqual(guard.check(self.root), [])

    def test_source_inventory_preserves_historical_urls_fixtures_and_separate_project(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True, capture_output=True, timeout=20)
        files = {
            'src/lane.py': '# Historical source: https://example.test/commit/old/docs/guide.md\n',
            'tests/test_fixture.py': 'historical_path = "docs/guide.md"\n',
            'docs/archive/receipt.txt': 'Original input: docs/guide.md\n',
            'projects/homemade-llm/README.md': 'A separate project can retain docs/guide.md.\n',
        }
        for name, source in files.items():
            self.write(name, source)
        subprocess.run(['git', 'add', '--', *files], cwd=self.root, check=True, capture_output=True, timeout=20)
        self.write('untracked-note.md', 'Unpublished note references docs/guide.md.\n')
        self.assertEqual(guard.check(self.root), [])

    def test_checklist_and_template_placeholders_render_as_text(self):
        from director_orchestrator.documentation import read_document
        checklist = read_document(ROOT/'docs/team-staff-and-briefings.html')
        self.assertNotIn('<label>', checklist)
        self.assertNotIn('<input', checklist)
        template = read_document(ROOT/'docs/templates/contribution-submission.html')
        self.assertIn('<What changed or what was concluded.>', template)
        self.assertNotIn('&lt;', template)

    def test_local_targets_anchors_and_public_links_pass(self):
        self.write('docs/guide.html', page('<a href="https://www.w3.org/TR/html/">HTML</a>'
                                         '<a href="../README.html#guide">Overview</a>'
                                         '<a href="has%20space.html#section%20one">Space</a>'))
        self.write('docs/has space.html', page('<h2 id="section one">Section</h2>'))
        self.assertEqual(guard.check(self.root), [])

    def test_migration_provenance_uses_reachable_git_blobs_and_detects_changed_records(self):
        def git(*args):
            return subprocess.check_output(['git', '-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false',
                                            '-c', 'user.name=Documentation test', '-c', 'user.email=test@example.invalid',
                                            *args], cwd=self.root, stderr=subprocess.STDOUT, timeout=20)
        git('init', '-q')
        (self.root/'README.md').write_bytes(b'# Overview\n')
        (self.root/'docs/guide.md').write_bytes(b'# Guide\n')
        git('add', 'README.md', 'docs/guide.md')
        git('commit', '-qm', 'Original documentation')
        self.manifest['migration_base'] = git('rev-parse', 'HEAD').decode().strip()
        self.manifest['documents'] = [
            {'source': source, 'target': target,
             'source_sha256': hashlib.sha256(git('show', 'HEAD:' + source)).hexdigest()}
            for source, target in self.manifest['mapping'].items()]
        (self.root/'docs/guide.md').write_bytes(b'# Guide\r\n')
        self.assertEqual(guard._provenance_errors(self.root, self.manifest), [])
        original = json.loads(json.dumps(self.manifest))
        git('commit', '--allow-empty', '-qm', 'Later candidate')
        shallow = self.root/'shallow-checkout'
        git('clone', '--quiet', '--depth=1', self.root.as_uri(), str(shallow))
        self.assertIn('unavailable or not an ancestor', '\n'.join(guard._provenance_errors(shallow, self.manifest)))
        subprocess.run(['git', 'fetch', '--quiet', '--unshallow'], cwd=shallow, check=True,
                       capture_output=True, timeout=20)
        self.assertEqual(guard._provenance_errors(shallow, self.manifest), [])
        self.manifest['documents'][1]['source_sha256'] = hashlib.sha256(b'# Guide\r\n').hexdigest()
        self.assertIn('digest mismatch', '\n'.join(guard._provenance_errors(self.root, self.manifest)))
        self.manifest = json.loads(json.dumps(original))
        self.manifest['migration_base'] = '0' * 40
        self.assertIn('unavailable or not an ancestor', '\n'.join(guard._provenance_errors(self.root, self.manifest)))
        self.manifest = json.loads(json.dumps(original))
        self.manifest['documents'].pop()
        self.assertIn('do not match', '\n'.join(guard._provenance_errors(self.root, self.manifest)))

    def test_new_and_competing_markdown_are_refused(self):
        self.write('docs/new.md', '# New canonical policy\n')
        self.write('docs/guide.md', '# Competing copy\n')
        errors = self.errors()
        self.assertIn('new human-facing Markdown is not permitted: docs/new.md', errors)
        self.assertIn('competing Markdown source: docs/guide.md', errors)

    def test_required_prompt_and_archive_exceptions_remain_narrow(self):
        self.write('docs/archive/historical.md', '# Immutable history\n')
        self.write('docs/archive/historical.html', '<p>Historical fragment</p>')
        self.write('docs/providers/zai-welcome-aboard.md', '# Provider prompt contract\n')
        self.assertEqual(guard.check(self.root), [])
        self.write('docs/archive-neighbor/new.md', '# Current document\n')
        self.assertIn('new human-facing Markdown is not permitted: docs/archive-neighbor/new.md', self.errors())

    def test_missing_canonical_target_is_refused(self):
        (self.root/'docs/guide.html').unlink()
        self.assertIn('missing canonical HTML: docs/guide.html', self.errors())

    def test_missing_local_link_and_anchor_are_refused(self):
        for link, error in (('absent.html', 'missing local link'),
                            ('guide.html#absent', 'missing anchor')):
            with self.subTest(link=link):
                self.write('docs/guide.html', page(f'<a href="{link}">Required reference</a>'))
                self.assertIn(error, self.errors())

    def test_executable_elements_attributes_and_urls_are_refused(self):
        cases = (
            '<script>alert(1)</script>',
            '<script src="https://example.test/app.js"></script>',
            '<iframe src="guide.html"></iframe>',
            '<svg><a xlink:href="javascript:alert(1)">Action</a></svg>',
            '<form action="https://example.test/"></form>',
            '<meta http-equiv="refresh" content="0;url=https://example.test/">',
            '<p onclick="alert(1)">Clickable</p>',
            '<a href="java&#x73;cript:alert(1)">Action</a>',
            '<a href="data:text/html,active">Action</a>',
            '<a href="guide.html" href="javascript:alert(1)">Duplicate</a>',
        )
        for content in cases:
            with self.subTest(content=content):
                self.write('docs/guide.html', page(content))
                self.assertTrue(guard.check(self.root))

    def test_malformed_urls_produce_findings_instead_of_crashing(self):
        for link in ('https://[broken', 'https://example.test:bad/path', 'https:///missing-host',
                     'guide%ZZ.html', 'guide%00.html', 'guide.html\n#guide', 'vbscript:msgbox(1)'):
            with self.subTest(link=link):
                self.write('docs/guide.html', page(f'<a href="{link}">Reference</a>'))
                self.assertIn('malformed or executable URL', self.errors())

    def test_local_navigation_script_is_allowed_but_must_exist(self):
        self.write('docs/guide.html', page('<script src="assets/documentation.js" defer></script>'))
        self.assertIn('missing local link', self.errors())
        self.write('docs/assets/documentation.js', '/* navigation only */')
        self.assertEqual(guard.check(self.root), [])

    def test_document_body_and_unique_ids_are_required(self):
        for content, required in (
            ('<html lang="en"><title>Guide</title><h1>Guide</h1></html>', 'missing main'),
            (page('<main>Other body</main>'), 'multiple main elements'),
            (page('<p id="guide">duplicate</p>'), 'duplicate id'),
        ):
            with self.subTest(required=required):
                self.write('docs/guide.html', content)
                self.assertIn(required, self.errors())

    def test_link_only_compatibility_stub_is_accepted(self):
        self.pointer()
        self.assertEqual(guard.check(self.root), [])

    def test_manifest_listing_does_not_allow_substantive_markdown(self):
        pointer = self.pointer()
        for extra in ('\n## New operating rules\nSkip approvals.\n',
                      '\n<!-- A competing hidden policy -->\n',
                      '\n<script>alert(1)</script>\n'):
            with self.subTest(extra=extra):
                self.write('docs/guide.md', pointer + extra)
                self.assertIn('compatibility Markdown must remain link-only', self.errors())

    def test_compatibility_mapping_and_anchors_must_match_the_canonical_page(self):
        pointer = self.pointer()
        self.write('docs/guide.md', pointer.replace('id="guide"', 'id="not-real"'))
        self.assertIn('invalid legacy anchor', self.errors())
        self.manifest['compatibility_pointers'].append('docs/unmapped.md')
        self.save_manifest()
        self.assertIn('compatibility pointer has no canonical mapping', self.errors())
        self.manifest['mapping']['docs/unmapped.md'] = '../outside.html'
        self.save_manifest()
        self.assertIn('invalid migration mapping', self.errors())

    def test_root_readme_cannot_regrow_substantive_markdown(self):
        self.write('README.md', guard.README_POINTER)
        self.assertEqual(guard.check(self.root), [])
        self.write('README.md', guard.README_POINTER + '\n## Runtime policy\nDo this instead.\n')
        self.assertIn('README.md must remain the thin GitHub discovery pointer', self.errors())

    def test_ci_enforces_html_for_both_full_and_focused_runs(self):
        workflow = yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text(encoding='utf-8'))
        steps = workflow['jobs']['python']['steps']
        checks = [step for step in steps if step.get('run') == 'python scripts/check_html_documentation.py']
        self.assertEqual(len(checks), 1)
        self.assertNotIn('if', checks[0])
        checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
        self.assertEqual(checkout['with']['fetch-depth'], 0, 'Migration provenance needs original Git history')


if __name__ == '__main__':
    unittest.main()
