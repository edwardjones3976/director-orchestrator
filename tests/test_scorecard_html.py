"""HTML is a safe display of sealed JSON, never an alternative trust signal."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
from html.parser import HTMLParser
import hashlib
import importlib.util
import io
import json
import re
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator import scorecard_html as html


def _g67b_diag(category, observe):
    """Best-effort sanitized observations; never expose an exception or affect checks.

    Only a fixed-pattern label and boolean values are ever written. Under
    GitHub Actions they go to the job summary, which the parallel runner's
    redacted log does not filter; elsewhere they go to stderr.
    """
    def emit(line):
        target = os.environ.get("GITHUB_STEP_SUMMARY")
        if target:
            with open(target, "a", encoding="utf-8") as summary:
                summary.write(line + "\n")
        else:
            print(line, file=sys.__stderr__, flush=True)
    try:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,96}", category):
            raise ValueError
        payload = observe()
        if payload is None:
            return
        if not payload or not all(isinstance(key, str) and re.fullmatch(r"[a-z0-9_]{1,48}", key)
                                  and isinstance(value, bool) for key, value in payload.items()):
            raise ValueError
        emit("WO-G67B-DIAG " + category + " " + " ".join(
            key + "=" + ("true" if value else "false") for key, value in sorted(payload.items())))
    except Exception:
        try:
            emit("WO-G67B-DIAG diag_error " + (category if re.fullmatch(r"[A-Za-z0-9_]{1,96}", category) else "invalid"))
        except Exception:
            pass


def _g67b_tempdir(tmp):
    path = Path(tmp)
    return {
        "tempdir_changes_under_resolve": str(path) != str(path.resolve()),
        "tempdir_has_tilde_component": any("~" in part for part in path.parts),
    }


def _g67b_path_pair(expected, actual):
    expected, actual = Path(expected), Path(actual)
    observations = {
        "expected_equals_actual": expected == actual,
        "expected_equals_actual_after_resolve": expected.resolve() == actual.resolve(),
    }
    if expected.exists() and actual.exists():
        observations["samefile"] = expected.samefile(actual)
    return observations


ROOT = Path(__file__).resolve().parents[1]


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.words = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.words.append(data)

    def visible(self):
        return " ".join(self.words)


class ScorecardHtmlTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.home = Path(self.folder) / "owner"
        self.owner_root = self.home / ".director-scorecard"
        (self.owner_root / "attestations").mkdir(parents=True)
        self.installed = self.owner_root / "launcher.py"
        self.installed.write_bytes((ROOT / "scripts/scorecard-launcher.py").read_bytes())
        self.launcher = html._launcher()
        self.enterContext(patch.object(self.launcher, "account_home", return_value=self.home))
        self.enterContext(patch.object(html, "_launcher", return_value=self.launcher))
        self.descriptor = self.owner_root / "release-descriptor.json"
        self.descriptor.write_text(json.dumps({"version": self.launcher.DESCRIPTOR_VERSION,
                                              "evaluator_sha": "a" * 40}), encoding="utf-8")
        self.fields = {"nonce": "b" * 64, "evaluator_sha": "a" * 40, "tree_id": "c" * 40,
                       "descriptor_sha256": hashlib.sha256(self.descriptor.read_bytes()).hexdigest(),
                       "launcher_sha256": hashlib.sha256(self.installed.read_bytes()).hexdigest()}
        self.report = {
            "schema": "director-completion-scorecard/v1", "generated_at": "2026-10-04T10:00:00+00:00",
            "attested": True, "attestation": self.fields, "candidate_sha": "d" * 40,
            "runtime_sha": "e" * 40, "image_id": "sha256:" + "f" * 64, "passed": 1, "percent": 5,
            "total": 20, "activation_authorized": False, "all_checks_verified": False,
            "checks": [{"id": "01", "title": "Service reachable", "status": "PASS",
                        "explanation": "A representative service observation.", "action": "Inspect the service.",
                        "evidence": [{"source": "https://example.test/receipt?q=a&state=ready", "sha256": "0" * 64,
                                      "observed_at": "2026-10-04T10:00:00+00:00",
                                      "expires_at": "2026-10-04T11:00:00+00:00", "runtime_sha": "e" * 40,
                                      "image_id": "sha256:" + "f" * 64}]},
                       {"id": "02", "title": "Inventory", "status": "UNKNOWN", "explanation": "Not collected.",
                        "action": "Collect the selected inventory.", "evidence": []}],
            "reference_files": [{"path": str(Path(self.folder) / "historical.md"), "sha256": "1" * 64}],
            "limitations": ["Evidence is time-specific."]}
        self.receipt_path = self.launcher._receipt_path(self.fields["nonce"])
        self.receipt = {"attestation": self.fields, "tree": str(ROOT), "report_sha256": None}
        self.receipt_path.write_text(json.dumps(self.receipt), encoding="utf-8")

    def seal(self, report=None):
        report = self.report if report is None else report
        self.receipt["report_sha256"] = self.launcher.report_digest(report)
        self.receipt_path.write_text(json.dumps(self.receipt), encoding="utf-8")

    def test_html_references_remain_unverified_and_do_not_execute_content(self):
        from director_orchestrator.completion_scorecard import _file_references
        folder = Path(self.folder) / "references"
        folder.mkdir()
        source = b'<html><script>throw new Error("must never execute")</script><p>20/20 PASS</p></html>'
        (folder / "claimed.html").write_bytes(source)
        (folder / "historical.md").write_text("Historical result", encoding="utf-8")
        (folder / ".private.html").write_text("Hidden", encoding="utf-8")
        rows = _file_references(folder)
        self.assertEqual({Path(row["path"]).name for row in rows}, {"claimed.html", "historical.md"})
        self.assertTrue(all(row["status"] == "UNVERIFIED_REFERENCE" for row in rows))
        current = next(row for row in rows if Path(row["path"]).name == "claimed.html")
        self.assertEqual(current["sha256"], hashlib.sha256(source).hexdigest())
        self.assertNotIn("checks", current)

    def test_pending_and_forged_counts_never_look_verified(self):
        for report in (self.report, {**self.report, "passed": 20, "percent": 100},
                       {**self.report, "attestation": {}}, {**self.report, "attested": False}):
            with self.subTest(report=report.get("passed")):
                document = Document(html.render_html(report))
                self.assertIn("Counts: UNATTESTED", document.visible())
                self.assertNotIn("/20 verified", document.visible())
                states = [attrs["class"] for tag, attrs in document.elements if tag == "td" and "class" in attrs]
                self.assertEqual(states, ["state unattested", "state unattested"])
        self.seal()
        forged = {**self.report, "passed": 20, "percent": 100}
        self.assertNotIn("/20 verified", html.render_html(forged))

    def test_sealed_html_has_identity_evidence_semantics_and_no_activation_authority(self):
        original = deepcopy(self.report)
        self.seal()
        source = html.render_html(self.report)
        parsed = Document(source)
        self.assertTrue(source.startswith("<!doctype html>"))
        self.assertIn(("html", {"lang": "en"}), parsed.elements)
        self.assertIn("1/20 verified (5%)", parsed.visible())
        for value in (self.report["candidate_sha"], self.report["runtime_sha"], self.report["image_id"],
                      self.fields["descriptor_sha256"], "NOT AUTHORIZED BY SCORECARD", "HTML is not evidence",
                      "Collect the selected inventory.", "Historical Markdown references remain readable"):
            self.assertIn(value, parsed.visible())
        self.assertIn(("th", {"scope": "row"}), parsed.elements)
        self.assertTrue(any(tag == "caption" for tag, _ in parsed.elements))
        self.assertFalse(any(tag in {"script", "iframe", "img", "form", "link"} for tag, _ in parsed.elements))
        self.assertEqual(self.report, original)

    def test_html_and_attribute_injection_are_escaped_and_unsafe_urls_are_not_links(self):
        payload = '<script>alert("x")</script><img src=x onerror="x">'
        report = deepcopy(self.report)
        report["candidate_sha"] = payload
        report["checks"][0].update(title=payload, explanation=payload)
        report["checks"][1].update(action=payload)
        sources = ["javascript:alert(1)", "data:text/html,<script>x</script>", "//host.invalid/file",
                   "file:///C:/private", "\\\\host.invalid\\private", "https://user:pass@example.test/x",
                   "https://example.test:\"bad/x", "https://example.test/\nscript", " https://example.test/",
                   'https://example.test/\" onclick=\"alert(1)']
        report["checks"][0]["evidence"] = [{"source": source} for source in sources]
        report["checks"][1]["evidence"] = [{"source": sources[0], "facts": {"payload": payload}}]
        report["reference_files"] = [{"path": payload, "sha256": payload}]
        report["limitations"] = [payload]
        self.seal(report)
        source = html.render_html(report)
        parsed = Document(source)
        self.assertNotIn("<script>", source)
        self.assertNotIn("<img ", source)
        self.assertIn(payload, parsed.visible())
        hrefs = [attrs["href"] for tag, attrs in parsed.elements if tag == "a"]
        self.assertEqual([value for value in hrefs if not value.startswith("#")], [sources[-1]])
        self.assertFalse(any(key.startswith("on") for _, attrs in parsed.elements for key in attrs))

    def patched_account_lookups(self):
        """Only the OS account lookup changes; the installed receipt verifier runs as written."""
        real_spec = importlib.util.spec_from_file_location
        home = self.home

        def spec(name, path, *args, **kwargs):
            value = real_spec(name, path, *args, **kwargs)
            if name == "scorecard_launcher":
                loader = value.loader

                class AccountLoader:
                    def create_module(self, module_spec):
                        return loader.create_module(module_spec)

                    def exec_module(self, module):
                        loader.exec_module(module)
                        module.account_home = lambda: home

                value.loader = AccountLoader()
            return value

        return patch.object(importlib.util, "spec_from_file_location", side_effect=spec)

    def test_cli_writes_html_and_json_only_until_launcher_seals(self):
        from director_orchestrator import completion_scorecard as sc

        spec = importlib.util.spec_from_file_location("scorecard_html_cli", ROOT / "scripts/director-scorecard.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        output = Path(self.folder) / "output"
        env = {self.launcher.NONCE_ENV: self.fields["nonce"], self.launcher.SHA_ENV: self.fields["evaluator_sha"],
               self.launcher.DESCRIPTOR_ENV: self.fields["descriptor_sha256"],
               "DIRECTOR_SCORECARD_RESULT": str(Path(self.folder) / "private-result.json")}
        # Collection is offline. The existing launcher-consistency checks have
        # their own real-Git tests; this fixture scopes only report publication.
        with patch.object(cli, "launch_refusal", return_value=None), \
                patch.object(sc, "_launcher", return_value=self.launcher), \
                patch.object(sc, "untrusted_checkout", return_value=None), \
                patch.dict(os.environ, env), \
                patch.object(sys, "argv", ["director-scorecard.py", "--candidate", "d" * 40,
                                            "--output-dir", str(output)]), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(cli.main(), 0)
        metadata = json.loads(stdout.getvalue().splitlines()[-1])
        _g67b_diag("scorecard_tempdir", lambda: _g67b_tempdir(self.folder))
        for kind in ("html", "json"):
            _g67b_diag("scorecard_" + kind, lambda kind=kind: _g67b_path_pair(
                output / ("completion-scorecard." + kind), metadata[kind]))
        self.assertEqual(Path(metadata["html"]), output / "completion-scorecard.html")
        self.assertNotIn("markdown", metadata)
        self.assertFalse((output / "completion-scorecard.md").exists())
        saved = json.loads((output / "completion-scorecard.json").read_bytes())
        self.assertIs(saved["attested"], True)
        self.assertIn("Counts: UNATTESTED", (output / "completion-scorecard.html").read_text(encoding="utf-8"))
        self.seal(saved)
        with self.patched_account_lookups():
            self.launcher._publish_html(ROOT, saved, ["--output-dir", str(output)])
        self.assertIn("0/20 verified", (output / "completion-scorecard.html").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((output / "completion-scorecard.json").read_bytes()), saved)
        self.assertTrue(self.launcher.report_is_attested(saved))

    def test_final_html_rejects_saved_json_substitution(self):
        self.seal()
        output = Path(self.folder) / "output"
        output.mkdir()
        (output / "completion-scorecard.json").write_text(json.dumps({**self.report, "passed": 20}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.launcher._publish_html(ROOT, self.report, ["--output-dir", str(output)])
        self.assertFalse((output / "completion-scorecard.html").exists())

    def test_launcher_seals_before_render_and_invalidates_failed_publication(self):
        # Use the actual renderer/launcher from a temporary archive; replace only
        # Git transport and the no-provider child with deterministic fixtures.
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            for relative in ("src/director_orchestrator/scorecard_html.py", "scripts/scorecard-launcher.py"):
                raw = (ROOT / relative).read_bytes()
                item = tarfile.TarInfo(relative)
                item.size = len(raw)
                bundle.addfile(item, io.BytesIO(raw))
        outside = Path(self.folder) / "outside"
        outside.mkdir()
        output = outside / "output"
        output.mkdir()
        provisional = []

        def git(repo, *args):
            if args[0] == "rev-parse":
                return ("c" * 40).encode()
            if args[0] == "archive":
                return archive.getvalue()
            return b""

        def child(command, **options):
            env = options["env"]
            receipt = json.loads(self.launcher._receipt_path(env[self.launcher.NONCE_ENV]).read_bytes())
            report = {**self.report, "attestation": receipt["attestation"]}
            Path(env["DIRECTOR_SCORECARD_RESULT"]).write_text(json.dumps(report), encoding="utf-8")
            (output / "completion-scorecard.json").write_text(json.dumps(report), encoding="utf-8")
            provisional.append(html.render_html(report))
            (output / "completion-scorecard.html").write_text(provisional[-1], encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.launcher, "_reject_git_metadata"))
            stack.enter_context(patch.object(self.launcher, "_git", side_effect=git))
            stack.enter_context(patch.object(self.launcher.subprocess, "run", side_effect=child))
            stack.enter_context(patch.object(self.launcher.Path, "cwd", return_value=outside))
            stack.enter_context(patch.object(self.launcher, "__file__", str(self.installed)))
            stack.enter_context(self.patched_account_lookups())
            self.assertEqual(self.launcher.launch(outside / "repo", ["--output-dir", str(output)], self.descriptor), 0)
            self.assertIn("Counts: UNATTESTED", provisional[-1])
            final = (output / "completion-scorecard.html").read_text(encoding="utf-8")
            self.assertIn("1/20 verified (5%)", final)
            saved = json.loads((output / "completion-scorecard.json").read_bytes())
            self.assertTrue(self.launcher.report_is_attested(saved))
            with patch.object(self.launcher, "_publish_html", side_effect=ValueError("publication failed")), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(self.launcher.launch(outside / "repo", ["--output-dir", str(output)], self.descriptor), 2)
            refused = json.loads((output / "completion-scorecard.json").read_bytes())
            self.assertFalse(self.launcher.report_is_attested(refused))
            self.assertNotIn("/20 verified", (output / "completion-scorecard.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
