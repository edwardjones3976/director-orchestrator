"""HTML document consumers preserve the authority-bearing structure."""

from html import escape
from pathlib import Path
import tempfile
import unittest

from director_orchestrator.documentation import embedded_json, html_text, read_document


class DocumentationTests(unittest.TestCase):
    def test_document_body_excludes_navigation_and_executable_text(self):
        source = """<!doctype html><html><head><title>Not authority</title>
        <style>.thing { content: 'not a requirement'; }</style></head><body>
        <header>Not authority</header><nav>Missing gate</nav><main>
        <article><h1 id="rules">Rules &amp; gates</h1><p>Require <strong>independent</strong>
        review of <code>head_sha</code>.</p><script>fake acceptance</script>
        <p hidden>Hidden pass</p></article></main><footer>Footer evidence</footer></body></html>"""
        text = html_text(source)
        self.assertIn("# Rules & gates", text)
        self.assertIn("Require **independent**\n        review of `head_sha`.", text)
        for absent in ("Not authority", "Missing gate", "fake acceptance", "Hidden pass", "Footer evidence"):
            self.assertNotIn(absent, text)
        self.assertIn("Require independent", html_text(source, structured=False))

    def test_list_membership_numbering_and_table_cells_remain_distinct(self):
        source = """<main><h2>Delegable</h2><ul><li>One exact act.</li>
        <li>Only <code>read()</code>.</li></ul><h2>Evidence</h2>
        <ol><li>CI</li><li>Review</li></ol><table><thead><tr><th>Phase</th><th>Executes</th></tr></thead>
        <tbody><tr><td>0</td><td>No</td></tr><tr><td>1</td><td>No</td></tr></tbody></table></main>"""
        text = html_text(source)
        self.assertIn("- One exact act.\n- Only `read()`.", text)
        self.assertIn("1. CI\n2. Review", text)
        self.assertIn("| Phase | Executes |\n| 0 | No |\n| 1 | No |", text)
        # Adding an act or changing a phase is observable, not discarded by the parser.
        self.assertIn("- New authority.", html_text(source.replace("</ul>", "<li>New authority.</li></ul>")))
        self.assertIn("| 1 | Yes |", html_text(source.replace("<td>1</td><td>No", "<td>1</td><td>Yes")))

    def test_heading_and_code_boundaries_are_not_supplied_by_links(self):
        source = '<main><h2 id="gate">Gate</h2><p>See <a href="other.html#gate">other guide</a>.</p><pre><code class="language-python">x = &quot;&lt;gate&gt;&quot;\n</code></pre><h2>Next</h2></main>'
        text = html_text(source)
        section = text.split("\n## Gate\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("[other guide](other.html#gate)", section)
        self.assertIn('```python\nx = "<gate>"\n```', section)
        self.assertNotIn("Next", section)

    def test_table_caption_is_visible_policy_content_before_its_rows(self):
        source = '<main><table><caption>Require <strong>independent</strong> review.</caption><tr><th>Gate</th><td>Required</td></tr></table></main>'
        for structured, caption in ((True, 'Require **independent** review.'),
                                     (False, 'Require independent review.')):
            with self.subTest(structured=structured):
                text = html_text(source, structured=structured)
                self.assertIn(caption, text)
                self.assertLess(text.index(caption), text.index('| Gate | Required |'))

    def test_html_requires_one_main_instead_of_accepting_chrome_as_content(self):
        for source in ("<nav>All checks pass</nav>", "<main>one</main><main>two</main>"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                html_text(source)
        self.assertIn("A fragment", html_text("<p>A fragment</p>", require_main=False))

    def test_history_boundaries_survive_and_script_fake_markers_do_not(self):
        source = """<main><!-- director-doc-history BEGIN: dated claim -->
        <p>Historical allocation</p><!-- director-doc-history END -->
        <p>Current allocation</p><script>"<!-- director-doc-history BEGIN: fake -->"</script></main>"""
        text = html_text(source, structured=False)
        self.assertIn("<!-- director-doc-history BEGIN: dated claim -->", text)
        self.assertIn("<!-- director-doc-history END -->", text)
        self.assertNotIn("fake", text)
        self.assertIn("Current allocation", text)

    def test_embedded_manifest_decodes_once_and_keeps_canonical_json_bytes(self):
        canonical = '{\n  "rule": "A < B & C",\n  "literal": "&lt;gate&gt;"\n}'
        source = '<main><!-- CONTRACT_BEGIN --><pre><code class="language-json">' + escape(canonical) + '\n</code></pre><!-- CONTRACT_END --></main>'
        self.assertEqual(embedded_json(source, "CONTRACT"), canonical)
        for changed in (
            source + "<!-- CONTRACT_BEGIN -->",
            source.replace("<!-- CONTRACT_END -->", ""),
            source.replace("language-json", "language-text"),
            source.replace("</pre>", '</pre><pre><code class="language-json">{}</code></pre>'),
        ):
            with self.subTest(source=changed), self.assertRaises(ValueError):
                embedded_json(changed, "CONTRACT")

    def test_required_markdown_and_machine_files_are_read_literally(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, content in (("AGENTS.md", "# Literal\n**Rule**\n"),
                                  ("profile.yaml", 'doc: "guide.html"\n'),
                                  ("guide.html", "<main><h1>Guide</h1><p>A &amp; B</p></main>")):
                path = root / name
                path.write_text(content, encoding="utf-8")
                actual = read_document(path)
                self.assertEqual(actual, content if path.suffix != ".html" else "\n# Guide\n\nA & B\n")


if __name__ == "__main__":
    unittest.main()
