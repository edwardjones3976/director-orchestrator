# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:17 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:00 UTC | BEGIN]]
"""Synthetic identity keys and offline API fixtures; no activation evidence."""
import base64
import copy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from director_orchestrator.ci_acceptance_issuer import (
    ISSUER, JWKS_URL, OIDC_VERSION, GitHubOIDCPolicy, collect_ci_evidence,
    oidc_audience, resolve_ci_authority, issue_ci_receipt,
)
from director_orchestrator.primary_activation_evidence import ActivationIdentity
from director_orchestrator.provider_acceptance_v2 import (
    DOMAIN, VERSION, ReviewContext, ProviderAcceptanceError, canonical,
)


class CIIssuerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = cls.rsa.public_key().public_numbers()
        def encode(n):
            return base64.urlsafe_b64encode(n.to_bytes((n.bit_length()+7)//8, 'big')).rstrip(b'=').decode()
        cls.jwks = {"keys": [{"kty": "RSA", "kid": "trusted", "n": encode(numbers.n), "e": encode(numbers.e)}]}

    def setUp(self):
        identity = ActivationIdentity("a"*40, "b"*40, "profile", "compose",
            "postgres:sha256:"+"c"*64, "12345678-1234-1234-1234-123456789abc", "primary", "checkpoint")
        self.context = ReviewContext(identity, "owner/repo", 100, "d"*64, "e"*64, 1000, 1300, frozenset({"openai"}))
        self.policy = GitHubOIDCPolicy("owner/repo", "123", "456", "repo:owner/repo:environment:ci-acceptance-issuer",
            "owner/repo/.github/workflows/issuer.yml@refs/heads/main", "9"*40,
            "github-ci", "isolated-ci", frozenset({"unit"}), 77, ".github/workflows/ci.yml", "8"*40)
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        self.claims = dict(iss=ISSUER, sub=self.policy.subject, aud=oidc_audience(self.public, self.context),
            iat=1000, nbf=1000, exp=1200, jti="unique", repository="owner/repo", repository_id="123",
            repository_owner_id="456", workflow_ref=self.policy.workflow_ref, workflow_sha=self.policy.workflow_sha,
            run_id="900", run_attempt="1")
        self.root = "/repos/owner/repo"
        self.api = {
            self.root: {"id":123, "owner":{"id":456}},
            self.root+"/actions/runs/10/attempts/2": {"id":10,"run_attempt":2,"workflow_id":77,
                "head_sha":"a"*40,"repository":{"id":123},"status":"completed","conclusion":"success","event":"push"},
            self.root+"/pulls/100": {"number":100,"head":{"sha":"a"*40,"repo":{"id":123}},"base":{"repo":{"id":123}}},
            self.root+"/actions/workflows/77": {"id":77,"path":".github/workflows/ci.yml"},
            self.root+"/git/commits/"+"a"*40: {"sha":"a"*40,"tree":{"sha":"b"*40}},
            self.root+"/contents/.github/workflows/ci.yml?ref="+"a"*40:{"sha":"f"*40},
            self.root+"/contents/.github/workflows/ci.yml?ref="+"8"*40:{"sha":"f"*40},
            self.root+"/actions/runs/10/attempts/2/jobs?per_page=100&page=1": {"total_count":1,"jobs":[
                {"name":"unit","run_id":10,"run_attempt":2,"head_sha":"a"*40,"status":"completed","conclusion":"success"}]},
        }

    def envelope(self, claims=None):
        return dict(version=OIDC_VERSION, public_key=self.public,
            token=jwt.encode(self.claims if claims is None else claims, self.rsa, algorithm="RS256", headers={"kid":"trusted"}))

    def resolve(self, envelope=None, **kwargs):
        with patch('director_orchestrator.ci_acceptance_issuer._read_json', return_value=self.jwks) as fetch:
            result = resolve_ci_authority(envelope or self.envelope(), expected=kwargs.get('context', self.context),
                                          policy=self.policy, now=kwargs.get('now', 1100))
            fetch.assert_called_once_with(JWKS_URL)
            return result

    def test_valid_identity_resolves_only_ci_key(self):
        authority = self.resolve()
        self.assertEqual((authority.role, authority.provider), ('ci', 'github-actions'))
        self.assertEqual(authority.public_key, base64.b64decode(self.public))

    def test_historical_readback_still_requires_current_github_signing_key(self):
        envelope=self.envelope()
        self.assertEqual(self.resolve(envelope,now=1100).public_key,base64.b64decode(self.public))
        with patch('director_orchestrator.ci_acceptance_issuer._read_json',return_value={'keys':[]}):
            with self.assertRaises(ProviderAcceptanceError):
                resolve_ci_authority(envelope,expected=self.context,policy=self.policy,now=1100)

    def test_claim_mutations_and_expiry_rejected(self):
        for field, value in [('repository_id','999'), ('repository_owner_id','999'), ('repository','other/repo'),
                ('sub','repo:owner/repo:ref:refs/heads/evil'), ('workflow_ref','evil'), ('workflow_sha','7'*40),
                ('aud','wrong'), ('iss','https://evil.invalid'), ('run_attempt','0'), ('exp',1100),
                ('iat',1101), ('nbf',1101), ('exp',1700)]:
            with self.subTest(field=field, value=value), self.assertRaises(ProviderAcceptanceError):
                self.resolve(self.envelope({**self.claims,field:value}))

    def test_key_and_context_cannot_be_substituted(self):
        envelope = self.envelope()
        envelope['public_key'] = base64.b64encode(bytes(32)).decode()
        with self.assertRaises(ProviderAcceptanceError): self.resolve(envelope)
        with self.assertRaises(ProviderAcceptanceError): self.resolve(context=replace(self.context,challenge='1'*64))
        with self.assertRaises(ProviderAcceptanceError): self.resolve(now=1300)

    def test_caller_key_material_and_wrong_algorithm_rejected(self):
        envelope = self.envelope()
        envelope['jwks'] = self.jwks
        with self.assertRaises(ProviderAcceptanceError): self.resolve(envelope)
        envelope = self.envelope()
        envelope['token'] = jwt.encode(self.claims, 'not-github'*4, algorithm='HS256')
        with self.assertRaises(ProviderAcceptanceError): self.resolve(envelope)
        other = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        envelope['token'] = jwt.encode(self.claims, other, algorithm='RS256', headers={'kid':'trusted'})
        with self.assertRaises(ProviderAcceptanceError): self.resolve(envelope)

    def collect(self):
        class Client:
            get = lambda _, path: copy.deepcopy(self.api[path])
        return collect_ci_evidence(Client(),expected=self.context,policy=self.policy,run_id=10,run_attempt=2)

    def test_api_evidence_binds_exact_check(self):
        self.assertEqual(self.collect()['checks'], [dict(name='unit',run_id=10,status='COMPLETED',conclusion='SUCCESS')])

    def test_subject_format_is_explicit_policy_not_legacy_inference(self):
        subject='repo:owner@456/repo@123:environment:ci-acceptance-issuer'
        self.policy=replace(self.policy,subject=subject)
        self.claims['sub']=subject
        self.assertEqual(self.resolve().role,'ci')

    def test_repository_owner_pr_and_tree_mismatch_rejected(self):
        original=copy.deepcopy(self.api)
        mutations=[(self.root,'id',999),
                   (self.root+'/git/commits/'+'a'*40,'tree',{'sha':'7'*40}),
                   (self.root+'/pulls/100','head',{'sha':'a'*40,'repo':{'id':999}})]
        for path,field,value in mutations:
            self.api=copy.deepcopy(original);self.api[path][field]=value
            with self.subTest(path=path),self.assertRaises(ProviderAcceptanceError):self.collect()
        self.api=copy.deepcopy(original);self.api[self.root]['owner']['id']=999
        with self.assertRaises(ProviderAcceptanceError):self.collect()

    def test_wrong_run_and_workflow_source_rejected(self):
        path = self.root+'/actions/runs/10/attempts/2'
        original = copy.deepcopy(self.api)
        for field,value in [('head_sha','b'*40),('run_attempt',1),('workflow_id',9),('event','pull_request'),('conclusion','failure')]:
            self.api=copy.deepcopy(original);self.api[path][field]=value
            with self.subTest(field=field),self.assertRaises(ProviderAcceptanceError):self.collect()
        self.api=copy.deepcopy(original)
        self.api[self.root+'/contents/.github/workflows/ci.yml?ref='+'a'*40]['sha']='7'*40
        with self.assertRaises(ProviderAcceptanceError):self.collect()

    def test_failed_duplicate_wrong_attempt_job_rejected(self):
        path=self.root+'/actions/runs/10/attempts/2/jobs?per_page=100&page=1'
        original=copy.deepcopy(self.api[path])
        for field,value in [('run_attempt',1),('head_sha','b'*40),('conclusion','skipped'),('status','in_progress')]:
            self.api[path]=copy.deepcopy(original);self.api[path]['jobs'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ProviderAcceptanceError):self.collect()
        self.api[path]={'total_count':2,'jobs':original['jobs']*2}
        with self.assertRaises(ProviderAcceptanceError):self.collect()

    def test_issuer_signs_observed_ci_only_with_bound_ephemeral_key(self):
        def read(url, headers=None):
            if url==JWKS_URL:return self.jwks
            if url.startswith('https://api.github.com'):return self.api[url.removeprefix('https://api.github.com')]
            from urllib.parse import urlsplit,parse_qs
            audience=parse_qs(urlsplit(url).query)['audience'][0]
            return {'value':jwt.encode({**self.claims,'aud':audience},self.rsa,algorithm='RS256',headers={'kid':'trusted'})}
        with patch('director_orchestrator.ci_acceptance_issuer._read_json',side_effect=read),patch('director_orchestrator.ci_acceptance_issuer.time.time',return_value=1100):
            result=issue_ci_receipt(expected=self.context,policy=self.policy,run_id=10,run_attempt=2,
                github_token='test-only',oidc_request_url=ISSUER+'/token?x=1',oidc_request_token='test-only')
        receipt=dict(result['receipt']);signature=base64.b64decode(receipt.pop('signature'))
        Ed25519PublicKey.from_public_bytes(base64.b64decode(result['ci_identity']['public_key'])).verify(signature,
            DOMAIN+canonical(dict(version=VERSION,context=self.context.document(),receipt=receipt)))
        self.assertEqual(receipt['role'],'ci')
        self.assertEqual(receipt['session_id'],'github-actions:900:1')
        self.assertEqual(receipt['ci_checks'][0]['run_id'],10)
        self.assertEqual(receipt['transport_evidence_digest'],hashlib.sha256(canonical(self.collect())).hexdigest())

    def test_production_cli_rejects_reduced_check_policy_before_network(self):
        from dataclasses import asdict
        from director_orchestrator.ci_acceptance_issuer import main
        policy=asdict(self.policy);policy['required_ci_checks']=list(policy['required_ci_checks'])
        with patch.dict(os.environ,{'CI_ISSUER_POLICY_JSON':json.dumps(policy)}),patch.object(sys,'argv',['issuer','--output','unused.json']):
            with self.assertRaisesRegex(ProviderAcceptanceError,'mandatory baseline'):
                main()


class WorkflowIsolationTests(unittest.TestCase):
    def setUp(self):
        self.workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/ci-acceptance-issuer.yml').read_text()

    def test_signer_cannot_install_dependencies_or_choose_mutable_source(self):
        preparation,signer=self.workflow.split('  issue:',1)
        self.assertNotIn('id-token: write',preparation)
        self.assertNotIn('pip install',signer)
        self.assertNotIn('CI_ISSUER_SOURCE_SHA',self.workflow)
        self.assertEqual(self.workflow.count('ref: ${{ github.workflow_sha }}'),2)
        self.assertIn('artifact-ids: ${{ needs.prepare.outputs.artifact_id }}',signer)
        self.assertIn('EXPECTED_RUNTIME_SHA256: ${{ needs.prepare.outputs.runtime_sha256 }}',signer)

    def test_actual_workflow_verifier_rejects_artifact_substitution_before_extract(self):
        blocks=re.findall(r"python3\.12 - <<'PY'\n(.*?)\n          PY",self.workflow,re.S)
        verifier=textwrap.dedent(blocks[1])
        for mutation in ('none','digest','source','run','attempt'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);prepared=root/'prepared-runtime';prepared.mkdir()
                archive=prepared/'issuer-runtime.tar.gz'
                with tarfile.open(archive,'w:gz') as bundle:
                    member=tarfile.TarInfo('ci-issuer-runtime/proof');member.size=4
                    bundle.addfile(member,io.BytesIO(b'safe'))
                digest=hashlib.sha256(archive.read_bytes()).hexdigest()
                manifest=dict(source_sha='a'*40,run_id='10',run_attempt='2',runtime_sha256=digest)
                field={'source':'source_sha','run':'run_id','attempt':'run_attempt'}.get(mutation)
                if field:manifest[field]='wrong'
                (prepared/'issuer-runtime-manifest.json').write_text(json.dumps(manifest))
                target=root/'runtime';target.mkdir()
                environment={**os.environ,'RUNNER_TEMP':str(target),'TRUSTED_SOURCE':'a'*40,
                    'GITHUB_RUN_ID':'10','GITHUB_RUN_ATTEMPT':'2',
                    'EXPECTED_RUNTIME_SHA256':('0'*64 if mutation=='digest' else digest)}
                result=subprocess.run([sys.executable,'-c',verifier],cwd=root,env=environment,capture_output=True)
                self.assertEqual(result.returncode==0,mutation=='none',result.stderr)
                self.assertEqual((target/'ci-issuer-runtime/proof').exists(),mutation=='none')


if __name__ == '__main__': unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 10:00 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 09:17 UTC | END]]
