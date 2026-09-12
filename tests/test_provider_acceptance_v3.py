# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 19:26 UTC | BEGIN]]
# [[OpenAI Codex mechanical recovery | GPT-6 | 2026-09-10 | 18:21 UTC | BEGIN]]
# [[Grok Bot | serving model unreported | 2026-09-10 | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | BEGIN]]
"""Synthetic signatures/CI keys only. Nothing here qualifies a real provider."""
import base64
import copy
from contextlib import closing
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from director_orchestrator import provider_acceptance_v3 as v3
from director_orchestrator import provider_acceptance_v2 as v2
from director_orchestrator.acceptance_journal import (
    REQUIRED_ACTIVATION_CHECKS, journal_from_environment, require_runtime_acceptance,
)
from director_orchestrator.config import Settings
from director_orchestrator.primary_activation_evidence import (
    ActivationEvidenceStore, ActivationEvidenceError, ActivationStage, canonical_facts_digest,
)
from director_orchestrator.ci_acceptance_issuer import oidc_audience
from tests import test_provider_acceptance_v2 as old_tests, test_ci_acceptance_issuer as ci_tests


def signed(key, domain, document):
    return dict(document=document, signature=base64.b64encode(key.sign(domain + v2.canonical(document))).decode())


class TwoPartyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ci_tests.CIIssuerTests.setUpClass()

    def setUp(self):
        self.old = old_tests.ProviderAcceptanceV2Tests()
        self.old.setUp()
        self.ci = ci_tests.CIIssuerTests()
        self.ci.setUp()
        self.now = int(time.time())
        self.context = replace(self.old.context, issued_at=self.now, expires_at=self.now + 3600)
        self.identity = self.context.activation
        self.keys = self.old.keys
        self.keys['ci'] = self.ci.key
        self.owner_key, self.observer_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        self.authorities = dict(self.old.authorities)
        self.authorities['security'] = replace(self.authorities['security'], provider='openai')
        self.authorities['independent_review'] = replace(self.authorities['independent_review'], provider='grok')
        self.ci.policy = replace(self.ci.policy, required_ci_checks=REQUIRED_ACTIVATION_CHECKS)
        self.authorities['ci'] = self.ci.policy.authority(base64.b64decode(self.ci.public))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry, self.db, self.path = self.root/'registry.json', self.root/'journal.sqlite3', self.root/'unused-v1.json'
        def anchor(key, name):
            return dict(principal=name+'-principal', deployment=name+'-deployment',
                public_key=base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
        self.policy = dict(version=v3.REGISTRY_VERSION, activation=asdict(self.identity),
            repository=self.context.repository, pull_request_number=self.context.pull_request_number,
            packet_digest=self.context.packet_digest, material_author_providers=['openai'],
            authorities={}, revoked_deployments=[], ci_oidc_policy=asdict(self.ci.policy),
            owner_authority=anchor(self.owner_key, 'owner'), observation_authority=anchor(self.observer_key, 'observer'))
        self.policy['ci_oidc_policy']['required_ci_checks'] = sorted(REQUIRED_ACTIVATION_CHECKS)
        for role, authority in self.authorities.items():
            fields = asdict(authority)
            fields['public_key'] = base64.b64encode(fields['public_key']).decode()
            fields['required_ci_checks'] = sorted(fields['required_ci_checks'])
            self.policy['authorities'][role] = fields
        self.adopt()
        self.env = patch.dict(os.environ, dict(LANGGRAPH_PRIMARY_ACCEPTANCE_MODE=v3.MODE,
            LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH=str(self.db),
            LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH=str(self.registry),
            LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256=self.pin(), DIRECTOR_COMPOSE_PROJECT_NAME='compose'))
        self.env.start(); self.addCleanup(self.env.stop)
        self.fetch = patch('director_orchestrator.ci_acceptance_issuer._read_json', return_value=self.ci.jwks)
        self.fetch.start(); self.addCleanup(self.fetch.stop)
        self.store = ActivationEvidenceStore(self.path)
        facts = {'preflight': True}
        self.store.record(self.identity, ActivationStage.PREFLIGHT, facts=facts, evidence_digest=canonical_facts_digest(facts))
        challenge = self.store._journal.challenge(self.identity, now=self.now)
        self.context = v2.ReviewContext(self.identity, self.context.repository, self.context.pull_request_number,
            self.context.packet_digest, challenge['challenge'], self.now, self.now+3600, frozenset({'openai'}))
        self.package = dict(version=v3.VERSION, context=self.context.document(), receipts={})
        for role, authority in self.authorities.items():
            receipt = dict(role=role, provider=authority.provider, principal=authority.principal,
                deployment=authority.deployment, session_id='github-actions:900:1' if role=='ci' else role+'-session',
                transport_evidence_digest='f'*64, ci_workflow_ref=authority.ci_workflow_ref,
                ci_workflow_commit=authority.ci_workflow_commit,
                verdict='SUCCESS' if role=='ci' else 'ANALYZED' if role=='security' else 'ACCEPT',
                ci_checks=[dict(name=name, run_id=10, status='COMPLETED', conclusion='SUCCESS')
                           for name in sorted(authority.required_ci_checks)])
            if role != 'ci':
                doc = dict(version=v3.OBSERVATION_VERSION, context=self.context.document(),
                    product='codex-security' if role=='security' else 'grok-bot', provider_family=authority.provider,
                    served_model='fixture-observed-model', session_id=receipt['session_id'],
                    transport='codex-security-product' if role=='security' else 'native-product-capture',
                    authentication='native-account-observed', capture_digest='f'*64,
                    installation_digest='1'*64, issued_at=self.now, expires_at=self.now+3600)
                receipt['native_observation'] = signed(self.observer_key, v3.OBSERVATION_DOMAIN, doc)
            if role == 'security':
                receipt['analysis'] = dict(scan_id='fixture-scan', manifest_digest='2'*64, report_digest='3'*64,
                    coverage_complete=True, unresolved_findings=0, author_family_analysis=True)
            self.package['receipts'][role] = receipt
        self.sign()
        self.ci.context = self.context
        self.ci.claims.update(iat=self.now, nbf=self.now, exp=self.now+300,
                             aud=oidc_audience(self.ci.public, self.context))

    def adopt(self):
        doc = dict(version=v3.ADOPTION_VERSION, decision='ADOPT_TWO_PARTY_V3', policy_commit='4'*40,
            policy_tree='5'*40, implementation_digest=v3.implementation_digest(),
            registry_digest=hashlib.sha256(v2.canonical({k:v for k,v in self.policy.items()
                if k not in {"owner_adoption", "revoked_deployments"}})).hexdigest(),
            **{k:self.policy[k] for k in ('activation','repository','pull_request_number','packet_digest')})
        self.policy['owner_adoption'] = signed(self.owner_key, v3.ADOPTION_DOMAIN, doc)

    def pin(self):
        self.registry.write_bytes(v2.canonical(self.policy))
        return hashlib.sha256(self.registry.read_bytes()).hexdigest()

    def sign(self):
        for role, receipt in self.package['receipts'].items():
            unsigned = {k:v for k,v in receipt.items() if k!='signature'}
            receipt['signature'] = base64.b64encode(self.keys[role].sign(v3.DOMAIN + v2.canonical(
                dict(version=v3.VERSION, context=self.package['context'], receipt=unsigned)))).decode()

    def verify(self, **kwargs):
        return v3.verify_two_party_acceptance(v2.canonical(self.package), expected=kwargs.get('expected',self.context),
            authorities=kwargs.get('authorities',self.authorities), now=kwargs.get('now',self.now), policy=self.policy)

    def accept(self):
        self.artifact = self.root/'acceptance.json'
        self.artifact.write_bytes(v2.canonical(dict(version=v3.ENVELOPE_VERSION,
            package=self.package, ci_identity=self.ci.envelope())))
        return self.store.record_acceptance_artifact(self.identity, self.artifact)

    def restore(self):
        self.accept()
        for stage, facts in ((ActivationStage.MIGRATED, {}),
            (ActivationStage.BACKED_UP, {'backup_digest':'sha256:'+'1'*64}),
            (ActivationStage.RESTORE_PROVED, {'backup_digest':'sha256:'+'1'*64,
                'required_control_plane_tables':17,'checkpoint_tables':4,'schema_evidence_rows':1})):
            self.store.record(self.identity, stage, facts=facts, evidence_digest=canonical_facts_digest(facts))

    def test_author_security_analysis_is_separate_from_one_independent_reviewer(self):
        self.assertEqual(self.verify().challenge, self.context.challenge)
        self.assertEqual(self.package['receipts']['security']['verdict'], 'ANALYZED')
        for authors in ({'openai','grok'}, {'grok'}):
            with self.subTest(authors=authors), self.assertRaisesRegex(v2.ProviderAcceptanceError,'material author'):
                self.verify(expected=replace(self.context,material_author_providers=frozenset(authors)))

    def test_unknown_family_and_aliases_never_qualify(self):
        with self.assertRaises(v2.ProviderAcceptanceError):
            replace(self.authorities['independent_review'], provider='grok-bot')
        saved = copy.deepcopy(self.package)
        for field, value in (('product','cursor-agent'),('product','grok'),('product','cursor-grok'),
                ('transport','direct-api'),('provider_family','unknown'),('served_model','unknown'),
                ('authentication','requested-native-label'),('session_id','another-session'),('capture_digest','0'*64)):
            self.package = copy.deepcopy(saved)
            envelope = self.package['receipts']['independent_review']['native_observation']
            envelope['document'][field] = value
            self.package['receipts']['independent_review']['native_observation'] = signed(
                self.observer_key,v3.OBSERVATION_DOMAIN,envelope['document'])
            self.sign()
            with self.subTest(field=field,value=value), self.assertRaises(v2.ProviderAcceptanceError): self.verify()

    def test_receipt_signer_cannot_manufacture_observer_or_owner_evidence(self):
        native = self.package['receipts']['independent_review']['native_observation']
        native['signature'] = signed(self.keys['independent_review'],v3.OBSERVATION_DOMAIN,native['document'])['signature']
        self.sign()
        with self.assertRaisesRegex(v2.ProviderAcceptanceError,'signature'): self.verify()
        adoption = self.policy['owner_adoption']
        adoption['signature'] = signed(self.keys['security'],v3.ADOPTION_DOMAIN,adoption['document'])['signature']
        with self.assertRaisesRegex(v2.ProviderAcceptanceError,'signature'): self.verify()

    def test_owner_adoption_requires_actual_code_and_exact_candidate(self):
        original = copy.deepcopy(self.policy['owner_adoption'])
        for field,value in (('implementation_digest','0'*64),('decision','IMPLEMENT_ONLY'),
                            ('packet_digest','0'*64),('policy_commit','main')):
            doc = copy.deepcopy(original['document']);doc[field]=value
            self.policy['owner_adoption']=signed(self.owner_key,v3.ADOPTION_DOMAIN,doc)
            with self.subTest(field=field), self.assertRaises(v2.ProviderAcceptanceError): self.verify()
        self.policy['owner_adoption']=original
        with patch('director_orchestrator.provider_acceptance_v3.implementation_digest',return_value='0'*64):
            with self.assertRaises(v2.ProviderAcceptanceError): self.verify()

    def test_missing_ci_security_and_misreported_author_analysis_stop(self):
        saved=copy.deepcopy(self.package)
        for field,value in (('coverage_complete',False),('unresolved_findings',1),('unresolved_findings',False),
                            ('author_family_analysis',False)):
            self.package=copy.deepcopy(saved)
            self.package['receipts']['security']['analysis'][field]=value;self.sign()
            with self.subTest(field=field), self.assertRaises(v2.ProviderAcceptanceError):self.verify()
        self.package=copy.deepcopy(saved)
        self.package['receipts']['ci']['ci_checks'].pop();self.sign()
        with self.assertRaises(v2.ProviderAcceptanceError):self.verify()
        self.package=copy.deepcopy(saved)
        self.policy['ci_oidc_policy']=None
        self.adopt()
        with self.assertRaisesRegex(v2.ProviderAcceptanceError,'OIDC'):self.verify()

    def test_old_versions_are_never_reinterpreted(self):
        self.package['version']=v2.VERSION;self.sign()
        with self.assertRaises(v2.ProviderAcceptanceError):self.verify()
        with self.assertRaises(v2.ProviderAcceptanceError):
            v2.verify_provider_acceptance(v2.canonical(self.package),expected=self.context,
                authorities=self.authorities,now=self.now)
        with patch.dict(os.environ,{'LANGGRAPH_PRIMARY_ACCEPTANCE_MODE':'provider-v2'}):
            with self.assertRaises(ActivationEvidenceError):journal_from_environment()
        with patch.dict(os.environ,{'LANGGRAPH_PRIMARY_ACCEPTANCE_MODE':'github-v1'}):
            with self.assertRaisesRegex(ActivationEvidenceError,'cannot ignore'):journal_from_environment()
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(Settings().langgraph_primary_acceptance_mode,'github-v1')
            self.assertIsNone(journal_from_environment())
            self.assertIsNone(require_runtime_acceptance(Settings()))

    def test_journal_restart_replay_failure_rollback_and_revocation(self):
        with patch.object(v3.TwoPartyAcceptanceJournal,'_write',side_effect=RuntimeError('fixture-crash')):
            with self.assertRaises(RuntimeError):self.accept()
        with closing(sqlite3.connect(self.db)) as db:
            self.assertIsNone(db.execute('SELECT accepted_at FROM acceptance_challenge').fetchone()[0])
        self.accept()
        self.assertEqual('ACCEPTED',ActivationEvidenceStore(self.path).load()['stages'][-1]['stage'])
        with self.assertRaises(ActivationEvidenceError):self.accept()
        self.policy['revoked_deployments']=[self.policy['observation_authority']['deployment']]
        with patch.dict(os.environ,{'LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256':self.pin()}):
            with self.assertRaises(ActivationEvidenceError):ActivationEvidenceStore(self.path).load()

    def test_replay_reauthenticates_ci_instead_of_trusting_database(self):
        self.accept()
        with patch('director_orchestrator.ci_acceptance_issuer._read_json',return_value={'keys':[]}):
            with self.assertRaises(ActivationEvidenceError):ActivationEvidenceStore(self.path).load()

    def test_runtime_requires_restore_real_inventory_and_generation_before_database(self):
        settings=replace(Settings(),langgraph_primary_enabled=True,allow_paid_runs=False,
            langgraph_primary_acceptance_mode=v3.MODE,langgraph_primary_acceptance_registry_path=str(self.registry),
            langgraph_primary_acceptance_registry_sha256=self.pin(),langgraph_primary_acceptance_journal_path=str(self.db),
            langgraph_primary_pinned_commit_sha=self.identity.candidate_commit,
            langgraph_primary_pinned_tree_sha=self.identity.candidate_tree,app_env=self.identity.profile_id,
            langgraph_primary_postgres_schema=self.identity.postgres_schema,
            langgraph_primary_checkpoint_namespace=self.identity.checkpoint_namespace)
        from director_orchestrator.primary_default import DefaultPrimaryActivation
        with patch('director_orchestrator.primary_default.resolve_default_primary_authority'), \
             patch('director_orchestrator.primary_default.build_typed_external_effect_grant'), \
             patch('director_orchestrator.primary_default.open_postgres_control_plane') as database:
            with self.assertRaises(ActivationEvidenceError):DefaultPrimaryActivation(settings)
            database.assert_not_called()
        self.restore()
        with self.assertRaisesRegex(ActivationEvidenceError,'prerequisites'):require_runtime_acceptance(settings)
        self.store.bind_legacy_inventory(self.identity,inventory_digest='8'*64,task_count=0)
        with patch('director_orchestrator.primary_generation.require_generation',side_effect=ActivationEvidenceError('generation required')):
            with self.assertRaisesRegex(ActivationEvidenceError,'generation required'):require_runtime_acceptance(settings)
        with patch('director_orchestrator.primary_generation.require_generation') as generation:
            self.assertEqual(require_runtime_acceptance(settings),self.identity)
            generation.assert_called_once_with(settings,self.identity)

    def test_codex_can_review_bot_authored_work_without_counting_security_as_review(self):
        self.context=replace(self.context,material_author_providers=frozenset({'grok'}))
        self.authorities['independent_review']=replace(self.authorities['independent_review'],provider='openai')
        self.policy['material_author_providers']=['grok']
        self.policy['authorities']['independent_review']['provider']='openai'
        self.adopt()
        self.package['context']=self.context.document()
        for role in ('security','independent_review'):
            receipt=self.package['receipts'][role]
            observation=receipt['native_observation']['document']
            observation['context']=self.context.document()
            if role=='security':receipt['analysis']['author_family_analysis']=False
            else:
                receipt['provider']='openai'
                observation.update(product='codex',provider_family='openai')
            receipt['native_observation']=signed(self.observer_key,v3.OBSERVATION_DOMAIN,observation)
        self.sign()
        self.assertEqual(self.verify().challenge,self.context.challenge)

    def test_candidate_challenge_expiry_and_signed_context_cannot_drift(self):
        for expected in (replace(self.context,challenge='0'*64),
                replace(self.context,activation=replace(self.identity,candidate_tree='0'*40)),
                replace(self.context,activation=replace(self.identity,database_instance_id='22345678-1234-1234-1234-123456789abc'))):
            with self.subTest(expected=expected),self.assertRaises(v2.ProviderAcceptanceError):self.verify(expected=expected)
        for now in (self.now-1,self.context.expires_at):
            with self.subTest(now=now),self.assertRaises(v2.ProviderAcceptanceError):self.verify(now=now)
        self.package['receipts']['independent_review']['session_id']='changed-after-signing'
        with self.assertRaises(v2.ProviderAcceptanceError):self.verify()

    def test_direct_verifier_revokes_every_authority_deployment(self):
        # Positive unrevoked control: empty revoked list still verifies.
        self.assertEqual(self.verify().challenge, self.context.challenge)
        cases = (
            ('owner', self.policy['owner_authority']['deployment']),
            ('observer', self.policy['observation_authority']['deployment']),
            ('security', self.authorities['security'].deployment),
            ('independent_review', self.authorities['independent_review'].deployment),
            ('ci', self.authorities['ci'].deployment),
        )
        saved = list(self.policy['revoked_deployments'])
        for label, deployment in cases:
            with self.subTest(authority=label):
                self.policy['revoked_deployments'] = [deployment]
                with self.assertRaisesRegex(v2.ProviderAcceptanceError, 'revoked'):
                    self.verify()
                self.policy['revoked_deployments'] = saved

    def test_product_grok_bot_requires_canonical_grok_family(self):
        # (a) independent_review enrolled as 'claude', product 'grok-bot' fails
        family = 'claude'
        self.context = replace(self.context, material_author_providers=frozenset({'openai'}))
        self.authorities['independent_review'] = replace(
            self.authorities['independent_review'], provider=family)
        self.policy['material_author_providers'] = ['openai']
        self.policy['authorities']['independent_review']['provider'] = family
        self.adopt()
        self.package['context'] = self.context.document()
        for role in ('security', 'independent_review'):
            receipt = self.package['receipts'][role]
            observation = receipt['native_observation']['document']
            observation['context'] = self.context.document()
            if role == 'security':
                receipt['analysis']['author_family_analysis'] = True
            else:
                receipt['provider'] = family
                observation.update(product='grok-bot', provider_family=family)
            receipt['native_observation'] = signed(
                self.observer_key, v3.OBSERVATION_DOMAIN, observation)
        self.sign()
        with self.assertRaises(v2.ProviderAcceptanceError):
            self.verify()

        # (b) independent_review enrolled as 'grok', product 'grok-bot' succeeds
        family = 'grok'
        self.authorities['independent_review'] = replace(
            self.authorities['independent_review'], provider=family)
        self.policy['authorities']['independent_review']['provider'] = family
        self.adopt()
        for role in ('security', 'independent_review'):
            receipt = self.package['receipts'][role]
            observation = receipt['native_observation']['document']
            if role != 'security':
                receipt['provider'] = family
                observation.update(product='grok-bot', provider_family=family)
            receipt['native_observation'] = signed(
                self.observer_key, v3.OBSERVATION_DOMAIN, observation)
        self.sign()
        self.assertEqual(self.verify().challenge, self.context.challenge)

        # (c) enrolled 'grok' but observation provider_family 'openai' is rejected
        saved = copy.deepcopy(self.package)
        envelope = self.package['receipts']['independent_review']['native_observation']
        envelope['document']['provider_family'] = 'openai'
        self.package['receipts']['independent_review']['native_observation'] = signed(
            self.observer_key, v3.OBSERVATION_DOMAIN, envelope['document'])
        self.sign()
        with self.assertRaises(v2.ProviderAcceptanceError):
            self.verify()
        self.package = saved
        self.sign()

        # Author-family independent review remains refused.
        with self.assertRaisesRegex(v2.ProviderAcceptanceError, 'material author'):
            self.verify(expected=replace(
                self.context, material_author_providers=frozenset({family})))

    def test_v3_prepare_offline_handoff_executes_real_journal_cli_and_inventory(self):
        from tests import test_windows_cutover_regressions as cutover
        if not cutover.PWSH:self.skipTest('PowerShell required')
        self.restore()
        legacy=self.root/'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as db:
            db.execute('CREATE TABLE tasks (task_id TEXT PRIMARY KEY,status TEXT)')
            db.execute("INSERT INTO tasks VALUES ('fixture-task','OWNER_DECISION_REQUIRED')")
            db.commit()
        # Only external CI JWKS retrieval is substituted in the child. Production
        # CLI, signed adoption/observations, transaction and inventory SQL execute.
        wrapper=self.root/'fixture-python.py'
        wrapper.write_text('''import json,os,runpy,sys
from unittest.mock import patch
args=sys.argv[1:]
with patch('director_orchestrator.ci_acceptance_issuer._read_json',return_value=json.loads(os.environ['TEST_CI_JWKS'])):
    if args[0]=='-m':
        args[args.index('--artifact')+1]=os.environ['TEST_EVIDENCE_ARTIFACT']
        sys.argv=args[1:]
        runpy.run_module(args[1],run_name='__main__')
    elif args[0]=='-c':exec(args[1])
    else:raise SystemExit('unexpected fixture invocation')
''')
        harness=cutover.OFFLINE_HANDOFF_HARNESS.replace('& $Python @command','& $Python $env:TEST_PYTHON_WRAPPER @command')
        source=self.root/'harness.ps1';source.write_text(harness)
        environment=self.root/'profile.env'
        environment.write_text('\n'.join(('ALLOW_PAID_RUNS=false',
            'LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED=false',
            'DIRECTOR_PROVIDER_VALIDATION_ENABLED=false','LANGGRAPH_PRIMARY_ACCEPTANCE_MODE='+v3.MODE,
            'APP_ENV=profile','LANGGRAPH_PRIMARY_POSTGRES_SCHEMA=primary','LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE=checkpoint')))
        identity=self.root/'identity.json';identity.write_text(json.dumps(asdict(self.identity)))
        events=self.root/'events.txt';events.write_text('')
        result=subprocess.run([cutover.PWSH,'-NoProfile','-NonInteractive','-File',str(source),
            str(cutover.ROOT/'scripts/windows-langgraph-primary-control.ps1'),str(environment),str(events),
            sys.executable,str(identity),'success'],capture_output=True,text=True,timeout=60,
            env=dict(os.environ,SQLITE_PATH=str(legacy),TEST_PYTHON_WRAPPER=str(wrapper),TEST_EVIDENCE_ARTIFACT=str(self.path),TEST_CI_JWKS=json.dumps(self.ci.jwks)))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(events.read_text().splitlines(),['require-staged','stop',
            'proof-1-slackbot','proof-1-n8n','proof-1-orchestrator','inventory',
            'bind-legacy-inventory','require-ready','proof-2-slackbot','proof-2-n8n','proof-2-orchestrator'])
        state=ActivationEvidenceStore(self.path).require_ready(self.identity)
        self.assertEqual(state['legacy_inventory']['digest'],hashlib.sha256(b'[["fixture-task","OWNER_DECISION_REQUIRED"]]').hexdigest())
        self.assertNotIn('ACTIVATED',[item['stage'] for item in state['stages']])


class TwoPartyGenerationTests(unittest.TestCase):
    def test_actual_compose_default_legacy_profile_is_not_a_signed_selection(self):
        import re
        compose=(Path(__file__).resolve().parents[1]/'docker-compose.yml').read_text()
        values={}
        for name in ('LANGGRAPH_PRIMARY_ACCEPTANCE_MODE','LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH',
                'LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256','LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH',
                'LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH','LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256'):
            value=re.search(r'^\s+'+name+r': (.*)$',compose,re.M).group(1)
            values[name]=value.split(':-',1)[1][:-1] if value.startswith('${') else value
        with patch.dict(os.environ,values,clear=True):
            self.assertEqual(Settings().langgraph_primary_acceptance_mode,'github-v1')
            self.assertIsNone(journal_from_environment())
            self.assertIsNone(require_runtime_acceptance(Settings()))
        from tests import test_windows_cutover_regressions as cutover
        if not cutover.PWSH:return
        # Execute the production selection switch, without executing setup or Docker.
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            scenarios=[values,dict(values,LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256='f'*64),
                dict(values,LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256='e'*64),
                dict(values,LANGGRAPH_PRIMARY_ACCEPTANCE_MODE='provider-v2'),
                dict(values,LANGGRAPH_PRIMARY_ACCEPTANCE_MODE=v3.MODE),
                dict(values,LANGGRAPH_PRIMARY_ACCEPTANCE_MODE='unknown')]
            fixtures=root/'profiles.json';fixtures.write_text(json.dumps(scenarios))
            harness=root/'selection.ps1';harness.write_text('''param($Source,$Profiles)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse error'}
$selection=@($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.SwitchStatementAst] -and $_.Condition.Extent.Text -ceq '$acceptanceMode'
})
if($selection.Count -ne 1){throw 'expected one selection switch'}
$results=@(foreach($values in (Get-Content -LiteralPath $Profiles -Raw | ConvertFrom-Json -AsHashtable)){
    $required=@();$acceptanceMode=$values.LANGGRAPH_PRIMARY_ACCEPTANCE_MODE
    try { & ([scriptblock]::Create($selection[0].Extent.Text));$true } catch {$false}
})
ConvertTo-Json -InputObject $results -Compress
''')
            result=subprocess.run([cutover.PWSH,'-NoProfile','-NonInteractive','-File',str(harness),
                str(cutover.ROOT/'scripts/windows-langgraph-primary-control.ps1'),str(fixtures)],
                capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[True,False,False,True,True,False])

    def test_legacy_mode_cannot_ignore_any_selected_signed_profile_marker(self):
        for marker in ('LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH','LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256',
                'LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH','LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH',
                'LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256'):
            with self.subTest(marker=marker),patch.dict(os.environ,{'LANGGRAPH_PRIMARY_ACCEPTANCE_MODE':'github-v1',marker:'fixture-only'},clear=True):
                with self.assertRaisesRegex(ActivationEvidenceError,'cannot ignore'):journal_from_environment()
                with self.assertRaisesRegex(ActivationEvidenceError,'cannot ignore'):require_runtime_acceptance(Settings())

    def test_uninstalled_native_v3_issuer_stops_before_identity_or_dispatch(self):
        from unittest.mock import Mock
        from director_orchestrator.native_acceptance_issuer import issue,initialize,collect,IssuerError
        cfg=Mock()
        with patch.dict(os.environ,{'LANGGRAPH_PRIMARY_ACCEPTANCE_MODE':v3.MODE}):
            for action in (lambda:issue(cfg,'fixture'),lambda:initialize(cfg),lambda:collect(cfg,'fixture')):
                with self.assertRaisesRegex(IssuerError,'not installed'):action()
        with patch.dict(os.environ,{'LANGGRAPH_PRIMARY_ACCEPTANCE_MODE':'Provider-v3-two-party'}):
            with self.assertRaisesRegex(IssuerError,'unknown'):issue(cfg,'fixture')
        cfg.identity.assert_not_called()

    def test_generation_preserves_mode_binding_and_exclusive_storage(self):
        from tests import test_primary_generation as generation_tests
        from director_orchestrator.primary_generation import require_generation,deployment_digest
        fixture=generation_tests.PrimaryGenerationTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        settings=replace(fixture.settings,langgraph_primary_acceptance_mode=v3.MODE)
        with self.assertRaises(ActivationEvidenceError):require_generation(settings,fixture.identity,reserve=True)
        self.assertFalse(fixture.journal.exists())
        fixture.manifest['deployment_configuration_sha256']=deployment_digest(settings)
        fixture.select_bytes()
        require_generation(settings,fixture.identity,reserve=True)
        require_generation(settings,fixture.identity)
        with self.assertRaises(ActivationEvidenceError):require_generation(settings,fixture.identity,reserve=True)
        with self.assertRaises(ActivationEvidenceError):require_generation(fixture.settings,fixture.identity)

    def test_ci_signs_only_selected_version_and_rejects_unknown_mode_before_network(self):
        import jwt
        from urllib.parse import urlsplit,parse_qs
        from director_orchestrator.ci_acceptance_issuer import issue_ci_receipt,ISSUER,JWKS_URL
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        ci_tests.CIIssuerTests.setUpClass()
        ci=ci_tests.CIIssuerTests();ci.setUp()
        def read(url,headers=None):
            if url==JWKS_URL:return ci.jwks
            if url.startswith('https://api.github.com'):return ci.api[url.removeprefix('https://api.github.com')]
            audience=parse_qs(urlsplit(url).query)['audience'][0]
            return {'value':jwt.encode({**ci.claims,'aud':audience},ci.rsa,algorithm='RS256',headers={'kid':'trusted'})}
        args=dict(expected=ci.context,policy=ci.policy,run_id=10,run_attempt=2,github_token='fixture-only',
            oidc_request_url=ISSUER+'/token',oidc_request_token='fixture-only')
        with patch('director_orchestrator.ci_acceptance_issuer._read_json',side_effect=read) as fetch, \
             patch('director_orchestrator.ci_acceptance_issuer.time.time',return_value=1100):
            with self.assertRaises(v2.ProviderAcceptanceError):issue_ci_receipt(**args,acceptance_mode='unknown')
            fetch.assert_not_called()
            result=issue_ci_receipt(**args,acceptance_mode=v3.MODE)
        receipt=dict(result['receipt']);signature=base64.b64decode(receipt.pop('signature'))
        key=Ed25519PublicKey.from_public_bytes(base64.b64decode(result['ci_identity']['public_key']))
        key.verify(signature,v3.DOMAIN+v2.canonical(dict(version=v3.VERSION,context=ci.context.document(),receipt=receipt)))
        with self.assertRaises(InvalidSignature):
            key.verify(signature,v2.DOMAIN+v2.canonical(dict(version=v2.VERSION,context=ci.context.document(),receipt=receipt)))
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 06:18 UTC | END]]
# [[Grok Bot | serving model unreported | 2026-09-10 | END]]
# [[OpenAI Codex mechanical recovery | GPT-6 | 2026-09-10 | 18:21 UTC | END]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-11 | 19:26 UTC | END]]
