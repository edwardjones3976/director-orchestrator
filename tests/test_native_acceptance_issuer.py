# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | BEGIN]]
"""Isolated fixture issuers only; no native login, provider or real signing key."""
import base64
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from director_orchestrator.native_acceptance_issuer import (Config, IssuerError,
    canonical, claim, collect, digest, initialize, issue, prepare_packet, write_new)
from director_orchestrator.native_acceptance_capture import (child_environment,
    parse_capture, parse_acp, prompt_for, run_bounded)
from director_orchestrator.primary_activation_evidence import ActivationIdentity
from director_orchestrator.provider_acceptance_v2 import DOMAIN, VERSION, ReviewContext


class IssuerTests(unittest.TestCase):
    def setUp(self):
        boundary = patch('director_orchestrator.native_acceptance_issuer.verify_boundary')
        boundary.start()
        self.addCleanup(boundary.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("broker", "worker", "requests", "responses", "public", "authorizations"):
            (self.root / name).mkdir()
        self.cfg = Config(self.root, "security", "claude", "S-1-5-21-1-2-3-1001",
                          "S-1-5-21-1-2-3-1002", "test-only-deployment", self.root,
                          "owner/repo", shutil.which("git"), "unused.exe", "a"*64,
                          None, None, "test-model")
        now = int(time.time())
        activation = ActivationIdentity("a"*40, "b"*40, "profile", "compose",
            "postgres:sha256:"+"c"*64, "12345678-1234-1234-1234-123456789abc", "primary", "checkpoint")
        self.context = ReviewContext(activation, "owner/repo", 1, digest(b"{}"), "e"*64,
                                     now-1, now+600, frozenset({"openai"}))
        self.request = dict(context=self.context.document(), packet="{}", run_nonce="f"*32)

    def capture(self):
        verdict = dict(verdict="ACCEPT", challenge=self.context.challenge,
                       packet_digest=self.context.packet_digest, run_nonce=self.request["run_nonce"])
        return dict(auth={"loggedIn": True, "authMethod": "claude.ai"}, events=[
            dict(type="system", subtype="init", model="test-model", session_id="native-session"),
            dict(type="assistant", message={"content": [{"type": "text", "text": json.dumps(verdict)}]}),
            dict(type="result", subtype="success", is_error=False, session_id="native-session", result=json.dumps(verdict))])

    def parse(self, capture):
        return parse_capture(canonical(capture), provider="claude", model="test-model", request=self.request, exit_code=0)

    def test_exact_native_success_and_tool_rejection(self):
        self.assertEqual(self.parse(self.capture()), "native-session")
        bad = self.capture()
        bad["events"][1]["message"]["content"] = [{"type": "tool_use", "name": "Read"}]
        with self.assertRaises(IssuerError): self.parse(bad)

    def test_native_session_auth_model_and_terminal_must_match(self):
        for mutate in (
            lambda c: c["auth"].update(authMethod="api_key"),
            lambda c: c["events"][0].update(model="other"),
            lambda c: c["events"][-1].update(session_id="forged"),
            lambda c: c["events"][-1].update(is_error=True),
            lambda c: c["events"].append(c["events"][-1]),
        ):
            c=self.capture(); mutate(c)
            with self.assertRaises(IssuerError): self.parse(c)

    def test_echoed_wrong_challenge_and_nonaccept_do_not_sign(self):
        for change in ({"challenge": "0"*64}, {"verdict": "CHANGES_REQUIRED"}, {"extra": "ignored"}):
            c=self.capture(); v=json.loads(c["events"][-1]["result"]);v.update(change)
            c["events"][-1]["result"]=json.dumps(v)
            with self.assertRaises(IssuerError):self.parse(c)

    def test_claim_blocks_repeat_even_without_completed_response(self):
        claim(self.root/'broker'/'claims.sqlite3', self.context.challenge)
        with self.assertRaises(IssuerError):claim(self.root/'broker'/'claims.sqlite3', self.context.challenge)

    def test_actual_sid_required_before_key_creation(self):
        with patch('director_orchestrator.native_acceptance_issuer.current_sid', return_value=self.cfg.worker_sid):
            with self.assertRaises(IssuerError):initialize(self.cfg)
        self.assertFalse((self.root/'broker'/'private.key').exists())

    def test_acl_rejects_coordinator_owner_key_read_and_parent_replacement(self):
        from director_orchestrator.native_acceptance_issuer import validate_acl_rows
        rule=dict(sid=self.cfg.broker_sid,rights=2032127,allow=True,inherit_only=False)
        row=dict(kind='private',owner=self.cfg.broker_sid,reparse=False,rules=[rule])
        validate_acl_rows([row],broker_sid=self.cfg.broker_sid)
        for mutate in (
            lambda r:r.update(owner='S-1-5-21-1-2-3-9999'),
            lambda r:r.update(reparse=True),
            lambda r:r['rules'].append(dict(sid=self.cfg.worker_sid,rights=1,allow=True,inherit_only=False)),
            lambda r:r.update(kind='ancestor',owner='S-1-5-32-544',rules=[dict(sid=self.cfg.worker_sid,rights=64,allow=True,inherit_only=False)]),
        ):
            bad=copy.deepcopy(row);mutate(bad)
            with self.assertRaises(IssuerError):validate_acl_rows([bad],broker_sid=self.cfg.broker_sid)

    def test_native_environment_does_not_inherit_signer_or_api_authority(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY":"secret", "CURSOR_API_KEY":"secret", "PYTHONPATH":"evil", "ISSUER_PRIVATE_KEY":"secret", "USERPROFILE":"native-home"}, clear=True):
            env=child_environment()
        self.assertEqual(env['USERPROFILE'],'native-home')
        self.assertFalse(any(k in env for k in ('ANTHROPIC_API_KEY','CURSOR_API_KEY','PYTHONPATH','ISSUER_PRIVATE_KEY')))

    def test_collect_signs_only_fixed_role_and_exact_worker_response(self):
        write_new(self.root/'authorizations'/(self.context.challenge+'.json'),dict(base='0'*40,context=self.context.document()))
        write_new(self.root/'requests'/(self.context.challenge+'.json'),self.request)
        response=dict(request_digest=digest(canonical(self.request)),worker_sid=self.cfg.worker_sid,
                      capture=base64.b64encode(canonical(self.capture())).decode(),exit_code=0)
        responsepath=self.root/'responses'/(self.context.challenge+'.json')
        write_new(responsepath,{**response,'worker_sid':'caller'})
        with patch('director_orchestrator.native_acceptance_issuer.current_sid',return_value=self.cfg.broker_sid):
            public=initialize(self.cfg)
            with self.assertRaises(IssuerError):collect(self.cfg,self.context.challenge)
            responsepath.write_bytes(canonical(response))
            artifact=collect(self.cfg,self.context.challenge)
        receipt=artifact['receipt']; signature=base64.b64decode(receipt.pop('signature'))
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public['public_key'])).verify(signature,DOMAIN+canonical(dict(version=VERSION,context=self.context.document(),receipt=receipt)))
        self.assertEqual(receipt['role'],'security')

    def test_issue_rejects_wrong_packet_before_predispatch_claim(self):
        write_new(self.root/'authorizations'/(self.context.challenge+'.json'),dict(base='0'*40,context=self.context.document()))
        with patch('director_orchestrator.native_acceptance_issuer.current_sid',return_value=self.cfg.broker_sid), patch('director_orchestrator.native_acceptance_issuer.prepare_packet',return_value=b'{"bad":true}'):
            with self.assertRaises(IssuerError):issue(self.cfg,self.context.challenge)
        self.assertFalse((self.root/'broker'/'claims.sqlite3').exists())

    def test_acp_complete_exchange_and_permission_rejection(self):
        result=json.loads(self.capture()['events'][-1]['result'])
        messages=[dict(id=1,result=dict(protocolVersion=1,authMethods=[dict(id='cursor_login')])),
                  dict(id=2,result={}),dict(id=3,result=dict(sessionId='acp-session')),
                  dict(id=4,result={}),dict(id=5,result={}),
                  dict(method='session/update',params=dict(sessionId='acp-session',update=dict(sessionUpdate='agent_message_chunk',content=dict(type='text',text=json.dumps(result))))),
                  dict(id=6,result=dict(stopReason='end_turn'))]
        cap=dict(protocol='cursor-acp/v1',messages=messages,mode='ask',model='configured')
        self.assertEqual(parse_acp(cap,request=self.request,model='configured'),'acp-session')
        for extra in (dict(id=9,method='session/request_permission'),dict(method='session/update',params=dict(sessionId='acp-session',update=dict(sessionUpdate='tool_call')))):
            bad=copy.deepcopy(cap);bad['messages'].insert(-1,extra)
            with self.assertRaises(IssuerError):parse_acp(bad,request=self.request,model='configured')
        bad=copy.deepcopy(cap);bad['messages']=[m for m in messages if m.get('id')!=4]
        with self.assertRaises(IssuerError):parse_acp(bad,request=self.request,model='configured')

    def test_full_immutable_git_packet_includes_deleted_and_added_content(self):
        def git(*a):
            return subprocess.check_output([self.cfg.git,'-C',str(self.root),*a],stderr=subprocess.DEVNULL).decode().strip()
        git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
        (self.root/'old.txt').write_text('before\n');git('add','old.txt');git('commit','-m','base')
        base=git('rev-parse','HEAD')
        git('rm','old.txt');(self.root/'new.txt').write_text('after\n');git('add','new.txt');git('commit','-m','head')
        head=git('rev-parse','HEAD')
        packet=prepare_packet(git=self.cfg.git,mirror=self.root,repository='owner/repo',base=base,head=head)
        files=json.loads(packet)['files']
        self.assertEqual([f['path'] for f in files],['new.txt','old.txt'])
        self.assertEqual(files[1]['base_text'],'before\n');self.assertIsNone(files[1]['head_text'])
        (self.root/'new.txt').write_text('uncommitted attack')
        self.assertEqual(packet,prepare_packet(git=self.cfg.git,mirror=self.root,repository='owner/repo',base=base,head=head))

    def test_duplicate_json_and_output_overflow_reject(self):
        from director_orchestrator.native_acceptance_issuer import read_json
        p=self.root/'duplicate.json';p.write_text('{"x":1,"x":2}')
        with self.assertRaises(IssuerError):read_json(p)
        import sys
        with self.assertRaises(IssuerError):run_bounded([sys.executable,'-c','print("x"*10000)'],cwd=self.root,env=child_environment(),limit=50)

    def test_prompt_contains_whole_packet_and_context_binding(self):
        prompt=prompt_for(self.request)
        self.assertIn(self.context.challenge,prompt)
        self.assertIn('BEGIN COMPLETE PACKET\n{}\nEND COMPLETE PACKET',prompt)


if __name__=='__main__':unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:18 UTC | END]]
