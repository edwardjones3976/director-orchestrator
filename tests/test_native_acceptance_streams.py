# [[Claude Code | Claude Opus 5 | 2026-09-07 | 05:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | BEGIN]]
"""Synthetic native events test parser contracts, not provider enrollment."""
import base64
import copy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator.native_acceptance_capture import parse_capture
from director_orchestrator.native_acceptance_issuer import (Config, IssuerError, MAX_CAPTURE,
    MAX_DOCUMENT, canonical, contained_native_path, digest)
from director_orchestrator.native_acceptance_streams import capture_document


class AdditionalNativeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.request = dict(context=dict(challenge="a"*64, packet_digest="b"*64), run_nonce="c"*32)
        self.verdict = json.dumps(dict(verdict="ACCEPT", **self.request["context"], run_nonce="c"*32))

    def capture(self, provider):
        if provider == "openai":
            events = [dict(type="thread.started", thread_id="native-session"), dict(type="turn.started"),
                      dict(type="item.completed", item=dict(id="i1", type="agent_message", text=self.verdict)),
                      dict(type="turn.completed", usage=dict(input_tokens=1, output_tokens=2))]
        else:
            events = [dict(event="init", conversation_id="native-session", init=dict(model="requested", cwd="fixture")),
                      dict(event="step_update", step_update=dict(conversation_id="native-session", step_type="agent_response", state="DONE")),
                      dict(event="result", result=dict(conversation_id="native-session", status="SUCCESS", response=self.verdict))]
        raw = b"\n".join(canonical(e) for e in events)
        return json.loads(capture_document(raw, provider=provider, model="requested", cwd="fixture",
            auth="Logged in using ChatGPT" if provider == "openai" else "native-cached-credentials-requested"))

    def parse(self, provider, capture):
        return parse_capture(canonical(capture), provider=provider, model="requested", request=self.request, exit_code=0)

    def test_native_success_preserves_unknown_observations(self):
        capture = self.capture("openai")
        self.assertEqual(self.parse("openai", capture), "native-session")
        self.assertIsNone(capture["observed_model"])
        self.assertIsNone(capture["observed_account"])

    # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
    def test_agy_stream_alone_can_never_authorize_a_receipt(self):
        # The structurally complete AGY capture still carries no independent
        # observation of the native account, so the lane fails closed instead of
        # returning a session for signature.
        capture = self.capture("gemini")
        self.assertEqual(capture["auth"], "native-cached-credentials-requested")
        with self.assertRaisesRegex(IssuerError, "lane unavailable"):
            self.parse("gemini", capture)

    def test_malformed_verdict_values_raise_controlled_issuer_errors(self):
        for provider in ("openai", "gemini"):
            for value in (None, 5, {"verdict": "ACCEPT"}, ["ACCEPT"], "not json at all"):
                capture = self.capture(provider)
                if provider == "openai":
                    capture["events"][2]["item"]["text"] = value
                else:
                    capture["events"][-1]["result"]["response"] = value
                with self.subTest(provider=provider, value=value):
                    with self.assertRaises(IssuerError):
                        self.parse(provider, capture)

    def test_malformed_stream_objects_raise_controlled_issuer_errors(self):
        for provider, mutate in (("openai", lambda c: c["events"][2].update(item="agent_message")),
                                 ("gemini", lambda c: c["events"][0].update(init="requested")),
                                 ("gemini", lambda c: c["events"][1].update(step_update="agent_response")),
                                 ("gemini", lambda c: c["events"][-1].update(result="SUCCESS"))):
            capture = self.capture(provider)
            mutate(capture)
            with self.subTest(provider=provider), self.assertRaises(IssuerError):
                self.parse(provider, capture)

    def test_full_raw_native_stream_is_not_rejected_by_the_document_bound(self):
        # A raw stream at the capture limit serializes larger than MAX_CAPTURE;
        # the document bound must reject only genuinely oversized evidence.
        def padded(size):
            capture = self.capture("openai")
            capture["events"].insert(2, dict(type="item.started",
                item=dict(id="pad", type="reasoning", text="x" * size)))
            return canonical(capture)
        raw = padded(MAX_CAPTURE)
        self.assertGreater(len(raw), MAX_CAPTURE)
        self.assertLess(len(raw), MAX_DOCUMENT)
        self.assertEqual(parse_capture(raw, provider="openai", model="requested",
                                       request=self.request, exit_code=0), "native-session")
        oversized = padded(MAX_DOCUMENT)
        self.assertGreater(len(oversized), MAX_DOCUMENT)
        with self.assertRaisesRegex(IssuerError, "native execution incomplete"):
            parse_capture(oversized, provider="openai", model="requested",
                          request=self.request, exit_code=0)

    def test_exact_closed_native_policy_shape_is_required(self):
        from director_orchestrator.antigravity_team_worker import DENY_TOOLS, is_deny_all_settings
        self.assertTrue(is_deny_all_settings({"permissions": {"allow": [], "deny": sorted(DENY_TOOLS)}}))
        for settings in (
            # Documented Antigravity settings keys can carry a command; surplus
            # keys are policy, not harmless decoration.
            {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS)}, "statusLine": {"type": "command", "command": "C:/foreign.exe"}},
            {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS)}, "title": "review"},
            {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS), "defaultMode": "acceptEdits"}},
            {"permissions": {"allow": ["read_file(*)"], "deny": sorted(DENY_TOOLS)}},
            {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS - {"mcp(*)"})}},
            {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS) + ["mcp(*)"]}},
            {"permissions": {"allow": [], "deny": [{"tool": "read_file(*)"}]}},
            {"permissions": {"allow": [], "deny": sorted(DENY_TOOLS)}, "permissions_extra": {}},
            {"permissions": []}, [], None,
        ):
            with self.subTest(settings=settings):
                self.assertFalse(is_deny_all_settings(settings))

    def test_native_paths_must_be_canonical_and_inside_protected_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            protected, elsewhere = root / "protected", root / "elsewhere"
            (protected / "home").mkdir(parents=True)
            elsewhere.mkdir()
            self.assertEqual(contained_native_path(str(protected / "home"), (str(protected),)),
                             protected / "home")
            traversal = str(protected / ".." / "elsewhere")
            # The corrected defect: lexical containment accepted this escape.
            self.assertTrue(Path(traversal).is_relative_to(protected))
            for value in (traversal, str(elsewhere), str(protected / "home" / "missing"),
                          "relative/home", "", None):
                with self.subTest(value=value), self.assertRaises(IssuerError):
                    contained_native_path(value, (str(protected),))
            with self.assertRaises(IssuerError):
                contained_native_path(str(protected / "home"), ())
            link = root / "link"
            try:
                os.symlink(elsewhere, link, target_is_directory=True)
            except (OSError, NotImplementedError, AttributeError):
                link = None
            if link is not None:
                with self.assertRaisesRegex(IssuerError, "link, junction or reparse|canonical location"):
                    contained_native_path(str(link), (str(root),))

    def test_config_load_rejects_native_policy_outside_the_protected_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            protected = root / "protected"
            (protected / "home").mkdir(parents=True)
            (root / "elsewhere").mkdir()
            for name in (protected / "deny.exe", root / "elsewhere" / "deny.exe"):
                name.write_bytes(b"fixture hook")
            cfg = Config(root, "security", "gemini", "S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1002",
                         "deployment", root, "owner/repo", "git", "native.exe", "a"*64, None, None,
                         "requested", (str(protected),), str(protected / "home"), "requested-project",
                         str(protected / "deny.exe"), "b"*64)
            def load(candidate):
                value = asdict(candidate)
                value["root"], value["mirror"] = str(root), str(root)
                raw = canonical(value)
                path = root / "issuer.json"
                path.write_bytes(raw)
                return Config.load(path, digest(raw))
            self.assertEqual(load(cfg).native_home, str(protected / "home"))
            for bad in (replace(cfg, native_home=str(protected / ".." / "elsewhere")),
                        replace(cfg, native_deny_hook=str(root / "elsewhere" / "deny.exe")),
                        replace(cfg, native_home=str(root / "elsewhere"))):
                with self.subTest(home=bad.native_home), self.assertRaises(IssuerError):
                    load(bad)

    def test_native_home_pinning_replaces_inherited_per_user_paths(self):
        from director_orchestrator.native_acceptance_capture import child_environment, pin_native_home
        with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\coordinator",
                                     "APPDATA": r"C:\Users\coordinator\AppData\Roaming",
                                     "LOCALAPPDATA": r"C:\Users\coordinator\AppData\Local"}, clear=True):
            env = child_environment()
        # Inherited per-user paths still name the coordinator account.
        self.assertEqual(env["APPDATA"], r"C:\Users\coordinator\AppData\Roaming")
        home = Path(r"C:\protected\native-home")
        pinned = pin_native_home(dict(env), home)
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            self.assertTrue(Path(pinned[key]) == home or home in Path(pinned[key]).parents, pinned[key])
    # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]

    def test_duplicate_terminal_init_truncated_and_extra_events_rejected(self):
        for provider in ("openai", "gemini"):
            for mutation in (lambda e:e+[e[-1]], lambda e:[e[0]]+e, lambda e:e[:-1], lambda e:e+[{"event":"unknown"}]):
                capture = self.capture(provider)
                capture["events"] = mutation(capture["events"])
                with self.subTest(provider=provider), self.assertRaises(IssuerError):
                    self.parse(provider, capture)

    def test_tool_and_unknown_stream_categories_rejected(self):
        for provider in ("openai", "gemini"):
            capture = self.capture(provider)
            if provider == "openai":
                capture["events"][2]["item"]["type"] = "command_execution"
            else:
                capture["events"][1]["step_update"]["step_type"] = "tool"
            with self.assertRaises(IssuerError): self.parse(provider, capture)

    def test_foreign_nonce_challenge_digest_verdict_or_model_rejected(self):
        for provider in ("openai", "gemini"):
            for key in ("run_nonce", "challenge", "packet_digest", "verdict"):
                capture = self.capture(provider)
                value = json.loads(self.verdict); value[key] = "foreign"
                if provider == "openai": capture["events"][2]["item"]["text"] = json.dumps(value)
                else: capture["events"][-1]["result"]["response"] = json.dumps(value)
                with self.subTest(provider=provider,key=key), self.assertRaises(IssuerError): self.parse(provider,capture)
            for key in ("requested_model", "observed_model", "observed_account"):
                capture = self.capture(provider); capture[key] = "invented"
                with self.assertRaises(IssuerError): self.parse(provider,capture)

    def test_codex_duplicate_verdict_and_api_auth_rejected(self):
        capture = self.capture("openai")
        capture["events"].insert(-1,copy.deepcopy(capture["events"][2]))
        with self.assertRaises(IssuerError): self.parse("openai",capture)
        capture = self.capture("openai"); capture["auth"] = "Logged in using an API key"
        with self.assertRaises(IssuerError): self.parse("openai",capture)

    def test_agy_foreign_session_and_conflicting_structured_verdict_rejected(self):
        capture = self.capture("gemini"); capture["events"][-1]["result"]["conversation_id"] = "foreign"
        with self.assertRaises(IssuerError): self.parse("gemini",capture)
        capture = self.capture("gemini"); capture["events"][-1]["result"]["structured_output"] = {"verdict":"ACCEPT"}
        with self.assertRaises(IssuerError): self.parse("gemini",capture)

    def test_duplicate_json_key_rejected_before_parser(self):
        raw = b'{"protocol":"codex-exec/v1","protocol":"forged"}'
        with self.assertRaises(ValueError): parse_capture(raw,provider="openai",model="requested",request=self.request,exit_code=0)

    def test_only_approved_role_provider_pairs_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            cfg=Config(root,"security","openai","S-1-5-21-1-2-3-1001","S-1-5-21-1-2-3-1002",
                       "deployment",root,"owner/repo","git","native.exe","a"*64,None,None,"requested",(str(root),))
            for role, provider, allowed in (("security","openai",True),("security","claude",True),
                                          ("independent_review","cursor",True),("independent_review","openai",False)):
                value=asdict(replace(cfg,role=role,provider=provider));value["root"]=str(root);value["mirror"]=str(root)
                raw=canonical(value);path=root/"issuer.json";path.write_bytes(raw)
                if allowed: self.assertEqual(Config.load(path,digest(raw)).provider,provider)
                else:
                    with self.assertRaises(IssuerError):Config.load(path,digest(raw))

    def test_native_workers_capture_actual_exchange_and_never_append_hook_to_argv(self):
        from tests.test_native_acceptance_issuer import IssuerTests
        from director_orchestrator.native_acceptance_capture import work
        from director_orchestrator.antigravity_team_worker import DENY_TOOLS
        # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | BEGIN]]
        # AGY is an enrollment-blocked lane: the protected policy is still
        # verified, but no native attempt is made and no response is written.
        for provider, surplus in (("openai", False), ("gemini", False), ("gemini", True)):
            fixture=IssuerTests(); fixture.setUp()
            try:
                home=Path(fixture.root).resolve()
                hook=home/"deny.exe";hook.write_bytes(b"fixture hook")
                cfg=replace(fixture.cfg,provider=provider,native_home=str(home),
                            native_project="requested-project",native_deny_hook=str(hook),
                            runtime_roots=(str(home),))
                (fixture.root/"requests"/(fixture.context.challenge+".json")).write_bytes(canonical(fixture.request))
                policy=fixture.root/".gemini/antigravity-cli";policy.mkdir(parents=True)
                settings={"permissions":{"allow":[],"deny":sorted(DENY_TOOLS)}}
                if surplus:settings["statusLine"]={"type":"command","command":"C:/foreign.exe"}
                (policy/"settings.json").write_bytes(canonical(settings))
                hooks={"director-team-deny-all":{"PreToolUse":[{"matcher":"*","hooks":[{"type":"command","command":cfg.native_deny_hook,"timeout":5}]}]}}
                (policy/"hooks.json").write_bytes(canonical(hooks))
                calls=[]
                def run(argv, **kwargs):
                    calls.append(argv)
                    if argv[-2:]==["login","status"]:return 0,b"Logged in using ChatGPT\n"
                    capture=self.capture(provider)
                    verdict=json.dumps(dict(verdict="ACCEPT",challenge=fixture.context.challenge,
                        packet_digest=fixture.context.packet_digest,run_nonce=fixture.request["run_nonce"]))
                    if provider=="openai":capture["events"][2]["item"]["text"]=verdict
                    else:
                        capture["events"][0]["init"].update(cwd=kwargs["cwd"],model=cfg.model)
                        capture["events"][-1]["result"]["response"]=verdict
                    return 0,b"\n".join(canonical(e) for e in capture["events"])
                with patch.object(Config,"identity"),patch.object(Config,"command",return_value=["native.exe"]),patch("director_orchestrator.native_acceptance_capture.run_bounded",side_effect=run):
                    if provider=="openai":
                        work(cfg,fixture.context.challenge)
                    else:
                        # Legacy requests without the pre-disclosure enrollment
                        # pin fail before configuration or provider execution.
                        expected="unknown worker request fields"
                        with self.subTest(surplus=surplus),self.assertRaisesRegex(IssuerError,expected):
                            work(cfg,fixture.context.challenge)
                written=(fixture.root/"responses"/(fixture.context.challenge+".json")).exists()
                self.assertEqual(written,provider=="openai")
                self.assertEqual(len(calls),2 if provider=="openai" else 0)
                self.assertTrue(all(cfg.native_deny_hook not in call for call in calls))
            finally:fixture.doCleanups()
        # [[Claude Code | claude-opus-5 | 2026-09-07 | 00:33 UTC | END]]

    # [[Claude Code | Claude Opus 5 | 2026-09-07 | 05:24 UTC | BEGIN]]
    def cursor_fixture(self, deny):
        from tests.test_native_acceptance_issuer import IssuerTests
        fixture = IssuerTests(); fixture.setUp()
        root = Path(fixture.root).resolve()
        profile = root / "worker-profile"
        (profile / "AppData" / "Roaming" / "Cursor").mkdir(parents=True)
        # The completed login is only located, never opened, moved or copied.
        (profile / "AppData" / "Roaming" / "Cursor" / "auth.json").write_text('{"fixture": true}')
        (root / "profile-without-appdata").mkdir()
        config = root / "cursor-config"; config.mkdir()
        (config / "cli-config.json").write_bytes(canonical({"permissions": {"allow": [], "deny": deny}}))
        (root / "requests" / (fixture.context.challenge + ".json")).write_bytes(canonical(fixture.request))
        cfg = replace(fixture.cfg, role="independent_review", provider="cursor", model="configured")
        return fixture, cfg, root, profile, config

    def acp_document(self, fixture, model):
        verdict = json.dumps(dict(verdict="ACCEPT", challenge=fixture.context.challenge,
            packet_digest=fixture.context.packet_digest, run_nonce=fixture.request["run_nonce"]))
        messages = [dict(id=1, result=dict(protocolVersion=1, authMethods=[dict(id="cursor_login")])),
                    dict(id=2, result={}), dict(id=3, result=dict(sessionId="acp-session")),
                    dict(id=4, result={}), dict(id=5, result={}),
                    dict(method="session/update", params=dict(sessionId="acp-session", update=dict(
                        sessionUpdate="agent_message_chunk", content=dict(type="text", text=verdict)))),
                    dict(id=6, result=dict(stopReason="end_turn"))]
        return canonical(dict(protocol="cursor-acp/v1", messages=messages, mode="ask", model=model))

    def cursor_attempt(self, deny, profile_kind):
        """Drive the actual worker through the capture/run_acp boundary."""
        from director_orchestrator.native_acceptance_capture import work
        fixture, cfg, root, profile, config = self.cursor_fixture(deny)
        try:
            attempts, sids, error = [], [], None
            def acp(command, *, cwd, env, prompt, model, timeout):
                attempts.append(dict(env=dict(env), cwd=cwd, model=model,
                    login_visible=(Path(env["APPDATA"]) / "Cursor" / "auth.json").is_file()))
                return 0, self.acp_document(fixture, model)
            def image_path(worker_sid):
                sids.append(worker_sid)
                if profile_kind == "absent":
                    raise IssuerError("trusted native worker profile metadata unavailable")
                return str(profile if profile_kind == "worker" else root / "profile-without-appdata")
            with patch.object(Config, "identity"), patch.object(Config, "command", return_value=["native.exe"]), \
                    patch("director_orchestrator.native_acceptance_capture._profile_image_path", side_effect=image_path), \
                    patch("director_orchestrator.native_acceptance_capture.run_acp", side_effect=acp):
                try:
                    work(cfg, fixture.context.challenge)
                except IssuerError as exc:
                    error = str(exc)
            response = root / "responses" / (fixture.context.challenge + ".json")
            return dict(error=error, attempts=attempts, sids=sids, worker_sid=cfg.worker_sid,
                        response=json.loads(response.read_bytes()) if response.exists() else None,
                        profile=profile, config=config)
        finally:
            fixture.doCleanups()

    def test_cursor_review_home_never_hides_the_native_worker_login(self):
        from director_orchestrator.native_acceptance_capture import CURSOR_DENY, pin_native_home
        outcome = self.cursor_attempt(CURSOR_DENY, "worker")
        self.assertIsNone(outcome["error"])
        env = outcome["attempts"][0]["env"]
        # Cursor computes its saved credential path from APPDATA, so the actual
        # worker roaming profile must survive the protected home pin.
        self.assertEqual(env["APPDATA"], str(outcome["profile"] / "AppData" / "Roaming"))
        self.assertTrue(outcome["attempts"][0]["login_visible"])
        # The location came from SID-keyed profile metadata, not the caller.
        self.assertEqual(outcome["sids"], [outcome["worker_sid"]])
        for key in ("HOME", "USERPROFILE"):
            self.assertEqual(env[key], str(outcome["config"]))
        self.assertEqual(env["CURSOR_CONFIG_DIR"], str(outcome["config"]))
        self.assertEqual(env["LOCALAPPDATA"], str(outcome["config"] / "AppData" / "Local"))
        self.assertFalse(any(k in env for k in ("CURSOR_API_KEY", "ANTHROPIC_API_KEY", "PYTHONPATH")))
        self.assertEqual(json.loads(base64.b64decode(outcome["response"]["capture"]))["protocol"], "cursor-acp/v1")
        # The stricter AGY pin is unchanged and still replaces every per-user path.
        strict = pin_native_home(dict(env), outcome["config"])
        self.assertEqual(strict["APPDATA"], str(outcome["config"] / "AppData" / "Roaming"))
        self.assertEqual(strict["LOCALAPPDATA"], str(outcome["config"] / "AppData" / "Local"))
        self.assertEqual(strict["HOME"], str(outcome["config"]))

    def test_cursor_missing_policy_or_profile_metadata_fails_closed(self):
        from director_orchestrator.native_acceptance_capture import CURSOR_DENY
        weakened = self.cursor_attempt([d for d in CURSOR_DENY if d != "Shell(*)"], "worker")
        self.assertIn("protected Cursor deny-all tool configuration missing", weakened["error"])
        # The weakened administrator configuration stops the lane before any
        # profile lookup, native attempt or response.
        self.assertEqual(weakened["sids"], [])
        self.assertEqual(weakened["attempts"], [])
        self.assertIsNone(weakened["response"])
        for profile_kind in ("absent", "unusable"):
            outcome = self.cursor_attempt(CURSOR_DENY, profile_kind)
            with self.subTest(profile_kind=profile_kind):
                self.assertIsNotNone(outcome["error"])
                self.assertEqual(outcome["attempts"], [])
                self.assertIsNone(outcome["response"])
    # [[Claude Code | Claude Opus 5 | 2026-09-07 | 05:24 UTC | END]]

    def test_hook_pin_is_verified_but_is_not_a_native_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);exe=root/"native.exe";hook=root/"deny.exe"
            exe.write_bytes(b"fixture executable");hook.write_bytes(b"fixture hook")
            cfg=Config(root,"security","gemini","broker","worker","deployment",root,
                "owner/repo","git",str(exe),digest(exe.read_bytes()),None,None,"requested",
                native_deny_hook=str(hook),native_deny_hook_digest=digest(hook.read_bytes()))
            self.assertEqual(cfg.command(),[str(exe)])
            hook.write_bytes(b"changed")
            with self.assertRaises(IssuerError):cfg.command()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:56 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-07 | 05:24 UTC | END]]
