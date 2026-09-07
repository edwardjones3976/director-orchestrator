# [[OpenAI Codex | GPT-6 | 2026-09-07 | 01:09 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:52 UTC | BEGIN]]
"""Synthetic independent signatures exercise consumption, never real enrollment."""
import base64
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from director_orchestrator.native_acceptance_issuer import Config, IssuerError, canonical, digest
from director_orchestrator.native_auth_enrollment import DOMAIN, VERSION, installation_binding, require_native_auth
import test_provider_acceptance_v2 as acceptance_fixtures
import test_native_acceptance_streams as stream_fixtures
from director_orchestrator.native_acceptance_capture import parse_capture


class NativeEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name).resolve()
        for name in ('code','native-auth','home'):
            (root/name).mkdir()
        for name in ('code/module.py','issuer.json','native.exe','deny-hook','home/credential.json','native-auth/readback.bin'):
            (root/name).write_bytes(b'synthetic fixture '+name.encode())
        self.cfg=Config(root,'security','gemini','S-1-5-21-1-2-3-1001','S-1-5-21-1-2-3-1002',
            'new-agy-installation',root/'mirror','owner/repo','git',str(root/'native.exe'),
            digest((root/'native.exe').read_bytes()),None,None,'requested',(str(root),),
            str(root/'home'),'native-project',str(root/'deny-hook'),digest((root/'deny-hook').read_bytes()),
            'synthetic-account','')
        fixture=acceptance_fixtures.ProviderAcceptanceV2Tests(); fixture.setUp(); self.fixture=fixture
        from director_orchestrator.acceptance_journal import REQUIRED_ACTIVATION_CHECKS
        fixture.authorities['ci']=replace(fixture.authorities['ci'],required_ci_checks=REQUIRED_ACTIVATION_CHECKS)
        fixture.package['receipts']['ci']['ci_checks']=[dict(name=n,run_id=1,status='COMPLETED',conclusion='SUCCESS') for n in sorted(REQUIRED_ACTIVATION_CHECKS)]
        fixture.sign()
        authorities={}
        for role, authority in fixture.authorities.items():
            item=asdict(authority); item['public_key']=base64.b64encode(item['public_key']).decode()
            item['required_ci_checks']=sorted(item['required_ci_checks']); authorities[role]=item
        self.registry=dict(version='director-native-auth-trust/v1',authorities=authorities,
            revoked_deployments=[],revoked_enrollments=[],adoption_context=fixture.context.document(),
            adoption_package=fixture.package,adopted_at=1100)
        self.boundary=patch('director_orchestrator.native_auth_enrollment.verify_boundary')
        self.boundary.start(); self.addCleanup(self.boundary.stop)
        self.save_registry()
        self.evidence=dict(version=VERSION,enrollment_id='synthetic-enrollment',authority_role='security',
            binding=installation_binding(self.cfg),issued_at=1100,expires_at=1200,
            authentication_mode='native-account',observation_source='native-authenticated-profile-and-project-readback',
            readback_digest=digest((root/'native-auth/readback.bin').read_bytes()),
            credential_files={'credential.json':digest((root/'home/credential.json').read_bytes())})
        self.save_evidence()

    def save_registry(self):
        root=self.cfg.root
        (root/'native-auth/registry.json').write_bytes(canonical(self.registry))
        self.cfg=replace(self.cfg,native_auth_registry_sha256=digest(canonical(self.registry)))
        (root/'issuer.json').write_bytes(json.dumps(asdict(self.cfg),default=str).encode())
        manifest=dict(commit='a'*40,tree='b'*40,broker_sid=self.cfg.broker_sid,worker_sid=self.cfg.worker_sid,
            files=[dict(path=name,digest=digest((root/name).read_bytes())) for name in ('issuer.json','code/module.py')])
        (root/'installation.json').write_bytes(canonical(manifest))

    def save_evidence(self):
        signature=self.fixture.keys['security'].sign(DOMAIN+canonical(self.evidence))
        self.envelope=dict(evidence=self.evidence,signature=base64.b64encode(signature).decode())
        (self.cfg.root/'native-auth/enrollment.json').write_bytes(canonical(self.envelope))

    def verify(self, **kw):
        return require_native_auth(self.cfg,now=1150,**kw)

    def test_independent_positive_path_unlocks_native_parser_without_invented_observation(self):
        verified=self.verify()
        fixture=stream_fixtures.AdditionalNativeCaptureTests(); fixture.setUp()
        fixture.request['native_auth_enrollment_digest']=verified.enrollment_digest
        capture=fixture.capture('gemini')
        self.assertIsNone(capture['observed_account'])
        self.assertEqual('native-session',parse_capture(canonical(capture),provider='gemini',model='requested',
            request=fixture.request,exit_code=0,native_auth=verified))

    def test_missing_expired_future_forged_and_foreign_evidence_rejected(self):
        for field,value in [('expires_at',1149),('issued_at',1151),('binding',{}),('version','foreign')]:
            old=self.evidence[field]; self.evidence[field]=value; self.save_evidence()
            with self.subTest(field=field),self.assertRaises(IssuerError): self.verify()
            self.evidence[field]=old
        self.save_evidence(); self.envelope['signature']=base64.b64encode(b'x'*64).decode()
        (self.cfg.root/'native-auth/enrollment.json').write_bytes(canonical(self.envelope))
        with self.assertRaises(IssuerError): self.verify()
        (self.cfg.root/'native-auth/enrollment.json').unlink()
        with self.assertRaises(IssuerError): self.verify()

    def test_revoked_or_self_adopted_authority_rejected(self):
        for name in ('revoked_deployments','revoked_enrollments'):
            self.registry[name]=[self.cfg.deployment if name=='revoked_deployments' else 'synthetic-enrollment']
            self.save_registry(); self.evidence['binding']=installation_binding(self.cfg); self.save_evidence()
            with self.subTest(name=name),self.assertRaises(IssuerError): self.verify()
            self.registry[name]=[]
        self.registry['authorities']['security']['provider']='gemini'; self.save_registry()
        with self.assertRaisesRegex(IssuerError,'self-adopt'): self.verify()

    def test_runtime_code_credentials_readback_and_registry_drift_rejected(self):
        for name in ('native.exe','deny-hook','code/module.py','home/credential.json','native-auth/readback.bin','native-auth/registry.json'):
            path=self.cfg.root/name; old=path.read_bytes(); path.write_bytes(old+b'changed')
            with self.subTest(name=name),self.assertRaises(IssuerError): self.verify()
            path.write_bytes(old)

    def test_valid_replacement_after_dispatch_rejected_by_attempt_pin(self):
        original=self.verify(); self.evidence['enrollment_id']='replacement'; self.save_evidence()
        self.verify()
        with self.assertRaisesRegex(IssuerError,'changed during'): self.verify(expected_digest=original.enrollment_digest)

    def test_missing_tree_in_legacy_installation_manifest_is_not_inferred(self):
        path=self.cfg.root/'installation.json'; manifest=json.loads(path.read_bytes()); del manifest['tree']
        path.write_bytes(canonical(manifest))
        with self.assertRaisesRegex(IssuerError,'another installation'): self.verify()

    def test_unsupported_other_provider_needs_no_new_enrollment(self):
        self.assertIsNone(require_native_auth(replace(self.cfg,provider='claude')))

    def test_fixture_resolves_alias_while_production_rejects_noncanonical_input(self):
        import os
        from director_orchestrator.native_acceptance_issuer import canonical_native_path
        # Every platform checks a noncanonical spelling. On Windows also exercise
        # the actual 8.3 alias that can be returned in hosted runner TEMP paths.
        spelling=str(self.cfg.root/'home'/'..')
        with self.assertRaises(IssuerError): canonical_native_path(spelling)
        self.assertEqual(self.cfg.root,canonical_native_path(str(Path(spelling).resolve())))
        if os.name == 'nt':
            import ctypes
            buffer=ctypes.create_unicode_buffer(32768)
            size=ctypes.windll.kernel32.GetShortPathNameW(str(self.cfg.root),buffer,len(buffer))
            self.assertGreater(size,0)
            self.assertLess(size,len(buffer))
            alias=buffer.value
            self.assertEqual(self.cfg.root,canonical_native_path(str(Path(alias).resolve())))
            # Volumes without short-name generation return the original path.
            # That is not an alias; no production acceptance rule is relaxed.
            if alias.casefold()!=str(self.cfg.root).casefold():
                with self.assertRaises(IssuerError): canonical_native_path(alias)

    def test_collect_rechecks_revocation_before_reading_or_signing_capture(self):
        from director_orchestrator.native_acceptance_issuer import collect
        original=self.verify()
        request=dict(native_auth_enrollment_digest=original.enrollment_digest)
        self.registry['revoked_deployments']=[self.cfg.deployment]; self.save_registry()
        with patch.object(Config,'identity'),patch('director_orchestrator.native_acceptance_issuer._job',return_value=({},self.fixture.context)),patch.object(Config,'read',return_value=request) as read,patch('director_orchestrator.native_auth_enrollment.time.time',return_value=1150):
            with self.assertRaisesRegex(IssuerError,'revoked'): collect(self.cfg,'a'*64)
            self.assertEqual(1,read.call_count)

    def test_issue_missing_enrollment_never_constructs_disclosure_packet(self):
        from director_orchestrator.native_acceptance_issuer import issue
        (self.cfg.root/'native-auth/enrollment.json').unlink()
        with patch.object(Config,'identity'),patch('director_orchestrator.native_acceptance_issuer._job',return_value=({'base':'c'*40},self.fixture.context)),patch('director_orchestrator.native_acceptance_issuer.prepare_packet') as prepare,patch('director_orchestrator.native_auth_enrollment.time.time',return_value=1150):
            with self.assertRaises(IssuerError): issue(self.cfg,'a'*64)
            prepare.assert_not_called()
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 00:52 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-07 | 01:09 UTC | END]]
