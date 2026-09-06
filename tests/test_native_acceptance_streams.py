# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | BEGIN]]
"""Synthetic native events test parser contracts, not provider enrollment."""
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator.native_acceptance_capture import parse_capture
from director_orchestrator.native_acceptance_issuer import Config, IssuerError, canonical, digest
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
        for provider in ("openai", "gemini"):
            capture = self.capture(provider)
            self.assertEqual(self.parse(provider, capture), "native-session")
            self.assertIsNone(capture["observed_model"])
            self.assertIsNone(capture["observed_account"])

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
        for provider in ("openai", "gemini"):
            fixture=IssuerTests(); fixture.setUp()
            try:
                cfg=replace(fixture.cfg,provider=provider,native_home=str(fixture.root),
                            native_project="requested-project",native_deny_hook="C:/protected/deny.exe")
                (fixture.root/"requests"/(fixture.context.challenge+".json")).write_bytes(canonical(fixture.request))
                policy=fixture.root/".gemini/antigravity-cli";policy.mkdir(parents=True)
                (policy/"settings.json").write_bytes(canonical({"permissions":{"allow":[],"deny":sorted(DENY_TOOLS)}}))
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
                    work(cfg,fixture.context.challenge)
                self.assertTrue((fixture.root/"responses"/(fixture.context.challenge+".json")).exists())
                self.assertEqual(len(calls),2 if provider=="openai" else 1)
                self.assertTrue(all(cfg.native_deny_hook not in call for call in calls))
            finally:fixture.doCleanups()

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
