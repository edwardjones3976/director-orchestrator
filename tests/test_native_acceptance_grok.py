# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:35 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 23:35 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:05 UTC | BEGIN]]
"""Documentation-derived synthetic ACP fixtures, never native capture evidence.

Source: installed xAI user-guide/14-headless-mode.md, streaming-json section.
No native execution, account qualification or effective tool denial is proved.
"""
import json
import uuid
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator.native_acceptance_capture import parse_capture, work
from director_orchestrator.native_acceptance_issuer import (Config, IssuerError, ROLE_PROVIDERS,
    _job, canonical, digest)
from director_orchestrator.native_acceptance_streams import (GROK_AUTH_LABEL, GROK_DENY_TOOLS,
    GROK_STREAM_EVENTS, GROK_TERMINAL_EVENTS, GROK_TOOL_EVENTS, capture_document,
    grok_attempt, grok_review_argv, grok_session_id, parse_grok_events)


class GrokSecurityIssuerTests(unittest.TestCase):
    model = "grok-4.6"

    def setUp(self):
        self.request = dict(context=dict(challenge="a"*64, packet_digest="b"*64), run_nonce="c"*32)
        self.verdict = json.dumps(dict(verdict="ACCEPT", **self.request["context"],
                                       run_nonce=self.request["run_nonce"]))

    def synthetic_events(self):
        """Documentation-derived ACP stream; never a captured native run."""
        return [dict(type="available_commands", commands=[]),
                dict(type="thought", data="synthetic reasoning"),
                dict(type="text", data=self.verdict),
                dict(type="usage", messageId="response-fixture", stopReason="end_turn"),
                dict(type="end", stopReason="end_turn", sessionId=grok_session_id(self.request["run_nonce"]),
                     requestId="request-fixture", num_turns=1,
                     modelUsage={self.model: {"modelCalls": 1}})]

    def synthetic_capture(self, events=None):
        raw = b"\n".join(canonical(e) for e in (self.synthetic_events() if events is None else events))
        return json.loads(capture_document(raw, provider="grok", model=self.model,
                                           cwd="synthetic-fixture", auth=GROK_AUTH_LABEL))

    def parse(self, capture, exit_code=0):
        return parse_capture(canonical(capture), provider="grok", model=self.model,
                             request=self.request, exit_code=exit_code)

    def test_documented_completion_is_structurally_valid_but_not_identity(self):
        self.assertEqual(GROK_TERMINAL_EVENTS, frozenset({"end"}))
        self.assertEqual(parse_grok_events(self.synthetic_events(), model=self.model,
                         request=self.request), grok_session_id(self.request["run_nonce"]))

    def test_missing_foreign_or_incomplete_terminal_fails(self):
        for mutation in (
            lambda es: es.pop(),
            lambda es: es.append(dict(type="end")),
            lambda es: es.append(dict(type="text", data="later")),
            lambda es: es[-1].update(stopReason="max_tokens"),
            lambda es: es[-1].update(stopReason="cancelled"),
            lambda es: es[-1].update(stopReason="refusal"),
            lambda es: es[-1].update(sessionId="foreign"),
            lambda es: es[-1].pop("requestId"),
            lambda es: es[-1].update(modelUsage={}),
            lambda es: es[-1].update(modelUsage={self.model: {"modelCalls": True}}),
            lambda es: es[-1].update(modelUsage={self.model: {"modelCalls": 1}, "other": {"modelCalls": 1}}),
            lambda es: es[-1].update(requestId=None),
            lambda es: es[-1].update(num_turns=2),
            lambda es: es[2].update(type=[]),
            lambda es: es[2].update(data=None),
            lambda es: es[-1].update(modelUsage={"other": {"modelCalls": 1}}),
            lambda es: es[-1].update(modelUsage={self.model: {"modelCalls": 2}}),
            lambda es: es[-1].update(num_turns=True),
            lambda es: es[-1].update(usage_is_incomplete=True),
            lambda es: es.insert(1, dict(type="error", message="failure")),
            lambda es: es.insert(1, dict(type="max_turns_reached")),
        ):
            events = self.synthetic_events()
            mutation(events)
            with self.subTest(events=events):
                with self.assertRaises(IssuerError):
                    parse_grok_events(events, model=self.model, request=self.request)

    def test_chunked_text_and_optional_plan(self):
        events = self.synthetic_events()
        events[2:3] = [dict(type="text", data=self.verdict[:8]),
                       dict(type="plan", entries=[]), dict(type="text", data=self.verdict[8:])]
        self.assertEqual(parse_grok_events(events, model=self.model, request=self.request),
                         grok_session_id(self.request["run_nonce"]))

    def test_messages_success_is_not_an_acp_completion(self):
        events = [dict(type="system", subtype="init", model=self.model),
                  dict(type="result", subtype="success", is_error=False, result=self.verdict)]
        with self.assertRaisesRegex(IssuerError, "unsupported native event"):
            parse_grok_events(events, model=self.model, request=self.request)

    def test_exactly_bound_accept_still_cannot_be_signed(self):
        capture = self.synthetic_capture()
        self.assertIsNone(capture["observed_model"])
        self.assertIsNone(capture["observed_account"])
        self.assertEqual(capture["auth"], GROK_AUTH_LABEL)
        with self.assertRaisesRegex(IssuerError, "lane unavailable"):
            self.parse(capture)

    def test_tool_events_are_rejected(self):
        for kind in sorted(GROK_TOOL_EVENTS):
            events = self.synthetic_events()
            events.insert(2, dict(type=kind, name="shell"))
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(IssuerError, "tools are prohibited"):
                    self.parse(self.synthetic_capture(events))

    def test_invented_terminal_and_unknown_events_are_rejected(self):
        # Non-ACP completion vocabulary cannot replace the documented end.
        for kind in ("result", "done", "completed", "turn.completed", "finish", None):
            events = self.synthetic_events()
            events.insert(2, dict(type=kind, session_id="invented", model=self.model))
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(IssuerError, "unsupported native event"):
                    self.parse(self.synthetic_capture(events))

    def test_foreign_challenge_packet_nonce_or_verdict_rejected(self):
        for key in ("challenge", "packet_digest", "run_nonce", "verdict"):
            value = json.loads(self.verdict)
            value[key] = "foreign"
            events = self.synthetic_events()
            events[2]["data"] = json.dumps(value)
            with self.subTest(key=key):
                with self.assertRaisesRegex(IssuerError, "did not accept exact challenge"):
                    self.parse(self.synthetic_capture(events))

    def test_partial_forged_and_malformed_captures_are_controlled(self):
        capture = self.synthetic_capture()
        with self.assertRaisesRegex(IssuerError, "native execution incomplete"):
            self.parse(capture, exit_code=1)
        for mutate in (lambda c: c.update(observed_account="invented-account"),
                       lambda c: c.update(observed_model=self.model),
                       lambda c: c.update(requested_model="other"),
                       lambda c: c.update(auth="Logged in"),
                       lambda c: c.update(protocol="agy-stream/v1"),
                       lambda c: c.update(cwd=""),
                       lambda c: c.update(events=[]),
                       lambda c: c.update(events=[c["events"][0]]),
                       lambda c: c.update(events="text"),
                       lambda c: c["events"][2].update(data=None),
                       lambda c: c["events"][2].update(data={"verdict": "ACCEPT"}),
                       lambda c: c.pop("cwd")):
            bad = self.synthetic_capture()
            mutate(bad)
            with self.subTest(mutate=mutate):
                with self.assertRaises(IssuerError):
                    self.parse(bad)

    def test_duplicate_json_field_rejected_before_the_parser(self):
        with self.assertRaises(ValueError):
            parse_capture(b'{"protocol":"grok-cli-stream/v1","protocol":"forged"}', provider="grok",
                          model=self.model, request=self.request, exit_code=0)

    def test_capture_document_refuses_an_unknown_provider(self):
        with self.assertRaises(IssuerError):
            capture_document(b"", provider="xai-api", model=self.model, cwd="fixture", auth="")

    def test_review_argv_is_the_build_cli_security_lane_never_the_api(self):
        argv = grok_review_argv(["C:/protected/grok/grok.exe"],
                                prompt_file="C:/work/review-prompt.txt", run_nonce="c"*32,
                                cwd="C:/work", model=self.model)

        def value(flag):
            self.assertIn(flag, argv)
            return argv[argv.index(flag) + 1]

        self.assertEqual(argv[0], "C:/protected/grok/grok.exe")
        self.assertEqual(value("--prompt-file"), "C:/work/review-prompt.txt")
        self.assertEqual(value("--cwd"), "C:/work")
        self.assertEqual(value("--output-format"), "streaming-json")
        self.assertEqual(value("--max-turns"), "1")
        self.assertNotIn("--tools", argv)
        self.assertEqual(value("--model"), self.model)
        self.assertEqual(value("--disallowed-tools"), GROK_DENY_TOOLS)
        self.assertEqual(value("--permission-mode"), "dontAsk")
        for flag in ("--no-subagents", "--disable-web-search"):
            self.assertIn(flag, argv)
        # Single-turn only: no resume, no imported session, no interactive shell.
        # The same set a bridge-attested Grok lane must never carry.
        from director_orchestrator.bridge_attestation import FORBIDDEN_ARGS
        for part in argv[1:]:
            self.assertNotIn(part.split("=", 1)[0].lower(), FORBIDDEN_ARGS)
        self.assertFalse(any("api.x.ai" in part or "http" in part for part in argv))

    def test_session_identifier_binds_this_exact_run_nonce(self):
        nonce = uuid.uuid4().hex
        self.assertEqual(grok_session_id(nonce), str(uuid.UUID(hex=nonce)))
        argv = grok_review_argv(["grok.exe"], prompt_file="p", run_nonce=nonce, cwd="C:/work", model=self.model)
        self.assertEqual(argv[argv.index("--session-id") + 1], str(uuid.UUID(hex=nonce)))
        for bad in ("", "C"*32, "c"*31, None, 5, uuid.uuid4().hex.upper()):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(IssuerError, "run nonce must bind"):
                    grok_session_id(bad)
                with self.assertRaises(IssuerError):
                    grok_review_argv(["grok.exe"], prompt_file="p", run_nonce=bad, cwd="C:/work", model=self.model)

    def test_attempt_helper_refuses_before_writing_a_prompt_or_running(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            with patch("director_orchestrator.native_acceptance_capture.run_bounded",
                       side_effect=lambda *a, **k: calls.append(a)):
                with self.assertRaisesRegex(IssuerError, "lane unavailable"):
                    grok_attempt(["grok.exe"], cwd=directory, env={}, model=self.model,
                                 request=dict(self.request, packet="{}"), timeout=600)
            self.assertEqual(calls, [])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def fixture(self):
        from tests.test_native_acceptance_issuer import IssuerTests
        fixture = IssuerTests()
        fixture.setUp()
        cfg = replace(fixture.cfg, provider="grok", model=self.model)
        return fixture, cfg

    def test_worker_refuses_before_claiming_or_dispatching_the_challenge(self):
        fixture, cfg = self.fixture()
        try:
            job = fixture.context.challenge
            (fixture.root / "requests" / (job + ".json")).write_bytes(canonical(fixture.request))
            calls = []
            with patch.object(Config, "identity"), \
                    patch.object(Config, "command", return_value=["grok.exe"]), \
                    patch("director_orchestrator.native_acceptance_capture.run_bounded",
                          side_effect=lambda *a, **k: calls.append(a)):
                with self.assertRaisesRegex(IssuerError, "lane unavailable"):
                    work(cfg, job)
            self.assertEqual(calls, [])
            # No paid attempt occurred, so the create-once claim is not spent and
            # no response exists for the broker to sign.
            self.assertFalse((fixture.root / "worker" / "claims.sqlite3").exists())
            self.assertFalse((fixture.root / "responses" / (job + ".json")).exists())
        finally:
            fixture.doCleanups()

    def test_grok_authored_candidates_are_excluded_from_this_issuer(self):
        fixture, cfg = self.fixture()
        try:
            job = fixture.context.challenge
            authored = replace(fixture.context, material_author_providers=frozenset({"grok"}))
            path = fixture.root / "authorizations" / (job + ".json")
            path.write_bytes(canonical(dict(base="0"*40, context=authored.document())))
            with self.assertRaisesRegex(IssuerError, "independent authorship mismatch"):
                _job(cfg, job)
            path.write_bytes(canonical(dict(base="0"*40, context=fixture.context.document())))
            self.assertEqual(_job(cfg, job)[1].challenge, job)
        finally:
            fixture.doCleanups()

    def test_only_the_security_role_and_a_single_pinned_executable_load(self):
        self.assertIn("grok", ROLE_PROVIDERS["security"])
        self.assertNotIn("grok", ROLE_PROVIDERS["independent_review"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            base = Config(root, "security", "grok", "S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1002",
                          "test-only-deployment", root, "owner/repo", "git", "grok.exe", "a"*64,
                          None, None, self.model, (str(root),))

            def load(candidate):
                value = asdict(candidate)
                value["root"], value["mirror"] = str(root), str(root)
                raw = canonical(value)
                path = root / "issuer.json"
                path.write_bytes(raw)
                return Config.load(path, digest(raw))

            self.assertEqual(load(base).provider, "grok")
            for bad, expected in ((replace(base, role="independent_review"), "fixed role"),
                                  (replace(base, entrypoint="index.js", entrypoint_digest="b"*64),
                                   "single pinned native executable"),
                                  (replace(base, worker_sid="S-1-5-21-1-2-3-1001"), "fixed role"),
                                  (replace(base, runtime_roots=()), "runtime roots")):
                with self.subTest(expected=expected):
                    with self.assertRaisesRegex(IssuerError, expected):
                        load(bad)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 06:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 23:35 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-25 | 02:35 UTC | END]]
