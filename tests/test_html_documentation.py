"""Canonical HTML is enforced by real files and meaningful refusal cases."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
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

    def test_local_targets_anchors_and_public_links_pass(self):
        self.write('docs/guide.html', page('<a href="https://www.w3.org/TR/html/">HTML</a>'
                                         '<a href="../README.html#guide">Overview</a>'
                                         '<a href="has%20space.html#section%20one">Space</a>'))
        self.write('docs/has space.html', page('<h2 id="section one">Section</h2>'))
        self.assertEqual(guard.check(self.root), [])

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


if __name__ == '__main__':
    unittest.main()
