# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 04:13 UTC | BEGIN]]
# [[OpenAI Codex integration | GPT-6 Astra | 2026-09-13 | 03:32 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | BEGIN]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:07 UTC | BEGIN]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:21 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:34 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 18:54 UTC | BEGIN]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 19:40 UTC | BEGIN]]
"""Drift tests for the descriptive provider profile sheets.

Every mirrored value in ``docs/provider-profiles/*.yaml`` is re-derived here
from the charter module, configuration defaults, adapter constants, and the
route catalog. The sheets grant no authority; these tests exist so that a
sheet can never silently disagree with the code that actually decides.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator

from director_orchestrator import provider_profiles as pp
from director_orchestrator.allocation_profiles import ALLOCATION_PROFILES, DEFAULT_ALLOCATION_PROFILE
from director_orchestrator.antigravity import (
    ANTIGRAVITY_CONTRACT_VERSION,
    ANTIGRAVITY_DEBATE_CONTRACT_VERSION,
    CAPABILITY_BY_KIND,
    DENIED_AUTHORITIES,
    WITHHELD_CONTEXT,
    AntigravityCapability,
    AntigravityInteractionKind,
)
from director_orchestrator.config import Settings
from director_orchestrator.contribution_tags import parse_contribution_tag, validate_inline_contribution_tags
from director_orchestrator.engine import _STANDING_PROVIDER_ROLES
from director_orchestrator.host_provider_bridge import SUPPORTED_HOST_LANES, _LANE_AUTH_ENV
from director_orchestrator.interactions import ProviderTransport
from director_orchestrator.models import AssignmentKind, ProviderName
from director_orchestrator.packet_routing import DirectorPacketTaskRouter
from director_orchestrator.primary_default import _PRIMARY_LANE_AGENT, _PRIMARY_PUBLIC_RESEARCH_LANES
from director_orchestrator.primary_effect_gateways import SUPPORTED_EFFECT_ACTIONS
from director_orchestrator.primary_transports import (
    ANTIGRAVITY_CLOSED_PAYLOAD_SCHEMA,
    PRIMARY_PROVIDER_LANES,
    PrimaryProviderCapability,
    _PROVIDER_AUTH_ENVIRONMENT,
)
from director_orchestrator.providers import provider_registry
from director_orchestrator.team_charter import team_operating_charter
from director_orchestrator.usage import ProviderDailySpendLedger
from director_orchestrator.workload_policy import Transport


ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = ROOT / "docs" / "provider-profiles"
SCHEMA_ROOT = ROOT / "docs" / "schemas"
SCHEMA_PATH = SCHEMA_ROOT / "director-provider-profile-v1.schema.json"
EXAMPLE_PATH = SCHEMA_ROOT / "director-provider-profile-v1.example.json"
ROLES_CONTRACT = ROOT / "docs" / "templates" / "project-bootstrap" / "provider-roles-contract.yaml"
SRC = ROOT / "src" / "director_orchestrator"
LANES = tuple(PRIMARY_PROVIDER_LANES)
COMMITTEE_LANES = ("antigravity", "claude", "cursor", "grok", "openai", "zai")
DEBATE_SEATS = COMMITTEE_LANES
PROPOSED_ROUTES = {
    "claude-director-injected-native-packet",
    "grok-bot",
    "claude-native-acceptance-issuer",
    "cursor-native-acceptance-issuer",
    "ci-acceptance-issuer",
}
SECRET_NAME = re.compile(
    r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|DSN|ALLOWLIST)"
    r"(?:$|_(?:FILE|PATH|JSON|B64|BASE64|VALUE)$)|(?:^|_)IDS?$"
)


def _settings() -> Settings:
    with patch.dict(os.environ, {"APP_ENV": "local", "SQLITE_PATH": "data/director.sqlite3"}, clear=True):
        return Settings()


def _enum_value(value):
    return getattr(value, "value", value)


def _profiles():
    return pp.load_profiles()


def _catalog():
    return pp.load_route_catalog()


def _routes(catalog):
    return {route["route_id"]: route for route in catalog["routes"]}


def _yaml_names(text: str) -> set[str]:
    return set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", text))


def _env_example() -> dict[str, str]:
    names = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]+)=(.*)$", line)
        if match:
            names[match.group(1)] = match.group(2)
    return names


def _config_variables(profile):
    for item in profile["models_and_effort"]["model_variables"]:
        yield item
    for item in profile["models_and_effort"]["effort_variables"]:
        yield item
    for item in profile["budgets_and_limits"]["variables"]:
        yield item
    for item in profile["gating"]["variables"]:
        yield item


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _walk_keys(value, key):
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                yield item
            yield from _walk_keys(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item, key)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.defs = self.schema["$defs"]

    def test_schema_is_valid_and_addressable(self):
        Draft202012Validator.check_schema(self.schema)
        self.assertTrue(self.schema["$id"])
        self.assertIn("profileDocument", self.defs)
        self.assertIn("routeCatalogDocument", self.defs)

    def test_schema_files_carry_no_inline_tags_and_have_examples(self):
        for path in SCHEMA_ROOT.glob("*.json"):
            with self.subTest(path=path.name):
                for line in path.read_text(encoding="utf-8").splitlines():
                    self.assertIsNone(parse_contribution_tag(line), path.name)
        for path in SCHEMA_ROOT.glob("*.schema.json"):
            with self.subTest(path=path.name):
                self.assertTrue(path.with_name(path.name.replace(".schema.json", ".example.json")).exists())

    def test_team_examples_validate_without_granting_authority(self):
        from director_orchestrator.antigravity_team import TeamGrant, ProviderBudget, ProviderUsageReceipt
        for name, model in (("antigravity-team-engineering-v1", TeamGrant),
                            ("primary-provider-budget-v1", ProviderBudget),
                            ("primary-provider-usage-v1", ProviderUsageReceipt)):
            with self.subTest(name=name):
                schema = json.loads((SCHEMA_ROOT / (name + ".schema.json")).read_text())
                document = json.loads((SCHEMA_ROOT / (name + ".example.json")).read_text())
                Draft202012Validator(schema).validate(document)
                model.model_validate_json(json.dumps(document))
                signature = "meter_signature" if name.endswith("usage-v1") else "owner_signature"
                self.assertEqual(document[signature], "0" * 64)

    def test_usage_schema_matches_closed_runtime_meter_evidence(self):
        from director_orchestrator.antigravity_team import ProviderUsageReceipt
        schema = json.loads((SCHEMA_ROOT / "primary-provider-usage-v1.schema.json").read_text())
        self.assertEqual(ProviderUsageReceipt.model_json_schema(), schema)
        self.assertFalse(schema["$defs"]["MeteringEvidence"]["additionalProperties"])

    def test_enums_mirror_runtime(self):
        self.assertEqual(self.defs["lane"]["enum"], list(LANES))
        self.assertEqual(
            self.defs["backendIdentity"]["enum"],
            ["gemini" if lane == "antigravity" else lane for lane in LANES],
        )
        self.assertEqual(
            self.defs["legacyAgent"]["enum"],
            [name.value for name in ProviderName if name.value != "owner"],
        )
        self.assertEqual(self.defs["providerTransportEnum"]["enum"], [item.value for item in ProviderTransport])
        self.assertEqual(self.defs["assignmentKind"]["enum"], [item.value for item in AssignmentKind])
        charter = team_operating_charter()
        self.assertEqual(
            self.defs["responsibilityArea"]["enum"],
            [area["name"] for area in charter["responsibility_areas"]],
        )
        # Committee keys name every debate seat: the weighted members plus any
        # seat without a configured weight (Z.ai).
        self.assertEqual(sorted(self.defs["committeeKey"]["enum"]),
                         sorted(charter["committee"]["debate"]["seatable_members"]))
        self.assertTrue(set(charter["committee"]["members"]) <= set(self.defs["committeeKey"]["enum"]))
        self.assertEqual(
            [item["const"] for item in self.defs["profileDocument"]["properties"]["authority"]["properties"]["universal_prohibited_actions"]["prefixItems"]],
            list(DirectorPacketTaskRouter._PROHIBITED_ACTIONS),
        )
        effects = self.defs["profileDocument"]["properties"]["authority"]["properties"]["external_effects"]["properties"]["may_be_granted"]["items"]["enum"]
        self.assertEqual(sorted(effects), sorted(action for _system, action in SUPPORTED_EFFECT_ACTIONS))
        self.assertEqual(
            self.defs["allocationProfileId"]["enum"], list(ALLOCATION_PROFILES)
        )
        self.assertEqual(self.defs["endpoint"]["enum"][: len(LANES)], list(LANES))


class LoaderTests(unittest.TestCase):
    def test_duplicate_keys_and_non_ascii_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "dup.yaml"
            duplicate.write_text("a: 1\na: 2\n", encoding="utf-8")
            with self.assertRaisesRegex(pp.ProviderProfileError, "duplicate"):
                pp.load_yaml_document(duplicate)
            unicode_doc = Path(directory) / "u.yaml"
            unicode_doc.write_text("a: café\n", encoding="utf-8")
            with self.assertRaisesRegex(pp.ProviderProfileError, "ASCII"):
                pp.load_yaml_document(unicode_doc)

    def test_every_document_validates_and_cross_validates(self):
        profiles = _profiles()
        catalog = _catalog()
        self.assertEqual(pp.cross_validate(profiles, catalog), [])
        self.assertEqual(sorted(profiles), sorted(LANES))
        self.assertEqual(list(catalog["lanes"]), list(LANES))

    def test_example_json_is_the_grok_projection(self):
        profiles = _profiles()
        expected = pp.render_example_json(profiles[pp.EXAMPLE_PROVIDER])
        self.assertEqual(EXAMPLE_PATH.read_text(encoding="utf-8").replace("\r\n", "\n"), expected)

    def test_profile_directory_holds_only_expected_files(self):
        names = sorted(path.name for path in PROFILE_ROOT.iterdir())
        expected = sorted(
            [pp.CATALOG_FILENAME, pp.INDEX_FILENAME]
            + [f"{lane}.yaml" for lane in LANES]
            + [f"{lane}{pp.SHEET_SUFFIX}" for lane in LANES]
        )
        self.assertEqual(names, expected)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.profiles = _profiles()
        self.settings = _settings()
        self.registry = {_enum_value(key) for key in provider_registry(self.settings)}
        self.roles = {_enum_value(key): value for key, value in _STANDING_PROVIDER_ROLES.items()}

    def test_identifiers_mirror_runtime(self):
        non_exempt_provider_enums = {"claude", "cursor", "grok", "gemini", "github-copilot"}
        for lane, profile in self.profiles.items():
            identifiers = profile["identifiers"]
            with self.subTest(lane=lane):
                self.assertEqual(profile["provider_id"], lane)
                self.assertEqual(identifiers["lane_id"], lane)
                self.assertEqual(identifiers["legacy_accounting_agent"], _PRIMARY_LANE_AGENT[lane])
                expected_enum = "gemini" if lane == "antigravity" else lane
                self.assertEqual(identifiers["provider_name_enum"], expected_enum)
                self.assertEqual(ProviderName(expected_enum).value, expected_enum)
                self.assertEqual(identifiers["has_legacy_adapter"], expected_enum in self.registry)
                if expected_enum in non_exempt_provider_enums:
                    self.assertIsNone(identifiers["standing_role_label"])
                else:
                    self.assertEqual(identifiers["standing_role_label"], self.roles.get(expected_enum))
                capability = PrimaryProviderCapability(
                    provider_id=lane,
                    backend_identity=identifiers["backend_identity"],
                    transport_kind="profile-test",
                    executable_observed=False,
                    authenticated_observed=False,
                )
                capability.validate()
                if lane != "antigravity":
                    with self.assertRaises(ValueError):
                        replace(capability, backend_identity="gemini").validate()

    def test_non_exempt_profiles_avoid_prescriptive_role_titles(self):
        non_exempt_lanes = {"claude", "cursor", "grok", "antigravity", "github-copilot"}
        banned_terms = (
            "project secretary",
            "chief programmer",
            "subordinate supplemental specialist",
            "primary teammate",
            "backup programmer",
            "acting director",
            "acting project director",
            "never a primary-team substitute",
            "supplemental only",
        )
        for lane in non_exempt_lanes:
            profile = self.profiles[lane]
            fields = [
                profile["display_name"],
                profile["roles"]["summary"],
                profile["roles"]["routing_rule"] or "",
                profile["identifiers"]["standing_role_label"] or "",
                json.dumps(profile["roles"]["non_substitution_rules"]),
                profile["authority"]["review_requirement"] or "",
                json.dumps(profile["authority"]["strategy_constraints"]),
            ]
            body = "\n".join(fields).lower()
            with self.subTest(lane=lane):
                for term in banned_terms:
                    self.assertNotIn(term, body)
                self.assertEqual(profile["roles"]["lane_class"], "capability_routed_lane")
                self.assertIsNone(profile["roles"].get("director_functions"))
                self.assertIsNone(profile["identifiers"]["standing_role_label"])

    def test_production_authority_mirrors_capability_validator(self):
        for lane, profile in self.profiles.items():
            capability = PrimaryProviderCapability(
                provider_id=lane,
                backend_identity=profile["identifiers"]["backend_identity"],
                transport_kind="profile-test",
                executable_observed=False,
                authenticated_observed=False,
                production_authority=True,
            )
            try:
                capability.validate()
                permitted = True
            except ValueError:
                permitted = False
            with self.subTest(lane=lane):
                self.assertEqual(profile["authority"]["production_authority_permitted"], permitted)
        refused = {lane for lane, profile in self.profiles.items() if not profile["authority"]["production_authority_permitted"]}
        self.assertEqual(refused, {"github-copilot"})


class CharterMirrorTests(unittest.TestCase):
    def setUp(self):
        self.profiles = _profiles()
        self.charter = team_operating_charter()
        self.directive = self.charter["director_operating_directive"]
        self.strategy = _settings().provider_strategy()
        self.contract = yaml.safe_load(ROLES_CONTRACT.read_text(encoding="utf-8"))

    def test_committee_mirrors_charter(self):
        members = self.charter["committee"]["members"]
        seatable = set(self.charter["committee"]["debate"]["seatable_members"])
        total = 0.0
        for lane, profile in self.profiles.items():
            committee = profile["roles"]["committee"]
            key = profile["identifiers"]["committee_member_key"]
            with self.subTest(lane=lane):
                # "member" means a configured voting weight; a debate seat is
                # separate, so a seat without a weight stays representable.
                self.assertEqual(committee["member"], key is not None and key in members)
                self.assertEqual(profile["capabilities"]["may_cast_committee_vote"], committee["member"])
                if key is None:
                    self.assertIsNone(committee["voting_power"])
                    self.assertFalse(committee["seatable_in_debate"])
                    continue
                self.assertEqual(key, lane)
                self.assertEqual(committee["seatable_in_debate"], key in seatable)
                if key not in members:
                    self.assertIsNone(committee["voting_power"])
                    self.assertTrue(committee["seatable_in_debate"])
                    continue
                self.assertEqual(committee["voting_power"], members[key]["voting_power"])
                total += committee["voting_power"]
        self.assertEqual(total, self.charter["committee"]["voting"]["configured_weight_total"])
        # 8.5 + Z.ai 1.5, owner decision 2026-09-27 "Approve 1.5; total 10.0".
        self.assertEqual(total, 10.0)

    def test_allocation_mirrors_allocation_profiles_and_directive(self):
        for lane, profile in self.profiles.items():
            allocation = profile["roles"]["allocation"]
            with self.subTest(lane=lane):
                for profile_id, weights in ALLOCATION_PROFILES.items():
                    self.assertEqual(allocation["profiles"][profile_id], weights[lane])
                self.assertEqual(allocation["default_profile"], DEFAULT_ALLOCATION_PROFILE)
                self.assertEqual(allocation["default_profile"], self.directive["default_allocation_profile"])
                self.assertEqual(allocation["capacity_context"], self.directive["capacity_context"])
                self.assertEqual(allocation["accounting_basis"], "admitted_assignment_count")
                self.assertEqual(allocation["overall_effort_policy"], self.directive["overall_effort_policy"])
                self.assertEqual(allocation["zero_target_rule"], self.directive["workload_enforcement"]["zero_target_rule"])
                lane_kind = self.directive["workload_lanes"].get(lane)
                self.assertEqual(profile["roles"]["charter_workload_lane"], lane_kind)
                self.assertEqual(profile["identifiers"]["charter_workload_lane_key"], lane if lane_kind else None)
        contract_weights = self.contract["allocation_and_selection"]["allocation_profiles"]
        self.assertEqual(contract_weights["lane_order"], list(LANES))
        for profile_id, weights in ALLOCATION_PROFILES.items():
            self.assertEqual(contract_weights["weights"][profile_id], [weights[lane] for lane in LANES])

    def test_role_profiles_mirror_charter(self):
        role_profiles = self.charter["role_profiles"]
        for lane, profile in self.profiles.items():
            roles = profile["roles"]
            with self.subTest(lane=lane):
                if lane in role_profiles:
                    expected = role_profiles[lane]
                    self.assertEqual(roles["capability_parity_target"], expected["capability_parity_target"])
                    self.assertEqual(roles["comparison_peers"], expected["comparison_peers"])
                    self.assertEqual(roles["elevated_work"], expected["elevated_work"])
                    self.assertEqual(roles["eligible_complexity"], expected["eligible_complexity"])
                    self.assertEqual(roles["eligible_responsibility_areas"], expected["eligible_responsibilities"])
                    self.assertEqual(roles["non_substitution_rules"], expected["non_substitution_rules"])
                    self.assertEqual(roles["routing_rule"], expected["routing_rule"])
                    self.assertEqual(roles["family"], expected.get("family"))
                    self.assertEqual(roles["transport_rule"], expected.get("transport_rule"))
                    self.assertEqual(roles["team"], expected["team"])
                else:
                    self.assertIsNone(roles["capability_parity_target"])
                    self.assertEqual(roles["comparison_peers"], [])
                    self.assertEqual(roles["elevated_work"], [])

    def test_strategy_fields_mirror_settings(self):
        seen = set()
        for lane, profile in self.profiles.items():
            key = profile["roles"]["strategy_key"]
            if key is None:
                self.assertEqual(profile["roles"]["team"], "lane_only", lane)
                continue
            seen.add(key)
            strategy = self.strategy[key]
            with self.subTest(lane=lane):
                self.assertEqual(profile["capabilities"]["strengths"], strategy["strengths"])
                self.assertEqual(profile["roles"]["strategy_priority"], strategy["priority"])
                self.assertEqual(profile["roles"]["team"], strategy["team"])
                self.assertEqual(profile["authority"]["strategy_constraints"], strategy.get("constraints", []))
                if "separation_of_duties" in strategy:
                    self.assertEqual(profile["authority"]["separation_of_duties"], strategy["separation_of_duties"])
                if "capability_parity_target" in strategy:
                    self.assertEqual(profile["roles"]["capability_parity_target"], strategy["capability_parity_target"])
                if "current_transport_constraints" in strategy:
                    api = next(item for item in profile["transports"] if item["enum_value"] == "xai_responses_api")
                    self.assertEqual(api["current_transport_constraints"], strategy["current_transport_constraints"])
        self.assertEqual(seen, set(self.strategy))

    def test_acting_director_functions_mirror_roles_contract_and_charter(self):
        director_lane = self.directive["director"]
        self.assertEqual(director_lane, "openai")
        functions = self.profiles[director_lane]["roles"]["director_functions"]
        appointed = self.contract["appointed_director"]
        self.assertEqual(appointed["provider"], "<provider key of the owner-appointed Director>")
        self.assertNotIn("functions", self.contract["providers"]["openai"])
        self.assertEqual(
            self.contract["providers"]["openai"]["coordination_functions"],
            "bound_by_appointed_director",
        )
        expected = appointed["functions"]
        self.assertEqual([item["id"] for item in functions], [item["id"] for item in expected])
        self.assertEqual([item["votes"] for item in functions], [item["votes"] for item in expected])
        participant_template = next(item for item in expected if item["id"] == "participant")
        self.assertEqual(
            participant_template["vote_weight"],
            "<charter voting_power of appointed_director.provider>",
        )
        self.assertNotIn("OpenAI", participant_template["boundary"])
        self.assertIn("appointed Director", participant_template["duties"][1])
        director = next(item for item in functions if item["id"] == "director")
        self.assertEqual(director["duties"], self.directive["duties"])
        participant = next(item for item in functions if item["id"] == "participant")
        self.assertEqual(participant["vote_weight"], self.charter["committee"]["members"][director_lane]["voting_power"])
        self.assertEqual(participant["vote_weight"], 2.5)
        for lane, profile in self.profiles.items():
            self.assertIsNone(profile["roles"]["openai_functions"], lane)
            if lane != director_lane:
                self.assertIsNone(profile["roles"].get("director_functions"), lane)

    def test_antigravity_constants_mirror_runtime(self):
        profile = self.profiles["antigravity"]
        self.assertEqual(profile["authority"]["denied_authorities"], sorted(DENIED_AUTHORITIES))
        self.assertEqual(profile["authority"]["withheld_context"], sorted(WITHHELD_CONTEXT))
        self.assertEqual(
            sorted(profile["capabilities"]["assignment_kinds"]),
            sorted(_enum_value(kind) for kind in CAPABILITY_BY_KIND),
        )
        self.assertEqual(profile["capabilities"]["antigravity_capabilities"], [item.value for item in AntigravityCapability])
        self.assertEqual(profile["capabilities"]["antigravity_interaction_kinds"], [item.value for item in AntigravityInteractionKind])
        api = next(item for item in profile["transports"] if item["enum_value"] == "gemini_interactions_api")
        for version in (ANTIGRAVITY_CONTRACT_VERSION, ANTIGRAVITY_DEBATE_CONTRACT_VERSION, ANTIGRAVITY_CLOSED_PAYLOAD_SCHEMA):
            self.assertIn(version, api["authority_note"])
        self.assertEqual(profile["authority"]["strategy_constraints"], self.strategy["gemini"]["constraints"])
        self.assertEqual(profile["capabilities"]["charter_allowed"], self.contract["providers"]["antigravity"]["allowed_work"])
        self.assertEqual(profile["authority"]["charter_prohibited"], self.contract["providers"]["antigravity"]["cannot_perform"])
        self.assertEqual(profile["authority"]["denied_authorities"], self.contract["providers"]["antigravity"]["denied_authorities"])
        self.assertEqual(profile["authority"]["withheld_context"], self.contract["providers"]["antigravity"]["withheld_context"])
        directive = _routes(_catalog())["antigravity-closed-work-directive"]
        self.assertEqual(directive["withheld_context"], sorted(WITHHELD_CONTEXT))
        self.assertEqual(directive["initiation"], "director")
        self.assertEqual(_routes(_catalog())["antigravity-invited-debate"]["initiation"], "director_invitation_only")
        for lane, other in self.profiles.items():
            if lane != "antigravity":
                self.assertEqual(other["authority"]["denied_authorities"], [], lane)
                self.assertEqual(other["authority"]["withheld_context"], [], lane)
                self.assertIsNone(other["capabilities"]["antigravity_capabilities"], lane)

    def test_perplexity_contract_mirrors_charter(self):
        profile = self.profiles["perplexity"]
        contract = self.charter["perplexity_contract"]
        self.assertEqual(profile["capabilities"]["charter_allowed"], contract["allowed"])
        self.assertEqual(profile["capabilities"]["required_evidence"], contract["required_evidence"])
        self.assertEqual(profile["authority"]["charter_prohibited"], contract["prohibited"])
        self.assertEqual(profile["authority"]["review_requirement"], contract["review_requirement"])
        self.assertEqual(profile["authority"]["charter_prohibited"], self.contract["providers"]["perplexity"]["prohibited"])
        self.assertEqual(
            profile["roles"]["excluded_responsibility_areas"],
            self.contract["providers"]["perplexity"]["excluded_responsibility_areas"],
        )
        browser = next(item for item in profile["transports"] if item["transport_id"] == "browser-provider-ui")
        self.assertEqual(browser["status"], "prohibited")
        rule = next(item for item in self.charter["browser_communication_policy"]["rules"] if item.startswith("Perplexity remains"))
        self.assertIn(rule, browser["authority_note"])
        self.assertEqual(profile["roles"]["transport_rule"], rule)

    def test_browser_transport_mirrors_charter_policy(self):
        policy = self.charter["browser_communication_policy"]
        eligible = {"antigravity" if item == "gemini" else item for item in policy["eligible_providers"]}
        implemented = set()
        for lane, profile in self.profiles.items():
            browsers = [item for item in profile["transports"] if item["transport_id"] == "browser-provider-ui"]
            with self.subTest(lane=lane):
                if not browsers:
                    self.assertNotIn(lane, eligible)
                    continue
                self.assertEqual(len(browsers), 1)
                browser = browsers[0]
                if browser["status"] == "implemented":
                    implemented.add(lane)
                    self.assertEqual(browser["browser_policy"]["primary"], policy["primary"])
                    self.assertEqual(browser["browser_policy"]["fallback"], policy["fallback"])
                    self.assertEqual(browser["browser_policy"]["owner_authorizable_actions"], policy["owner_authorizable_actions"])
                else:
                    self.assertEqual(browser["status"], "prohibited")
                    self.assertIsNone(browser["browser_policy"])
        self.assertEqual(implemented, eligible)
        self.assertEqual(sorted(_routes(_catalog())["browser-provider-ui"]["providers_involved"]), sorted(eligible))
        self.assertIs(list(Transport)[-1], Transport.BROWSER)

    def test_copilot_lite_block_mirrors_directive_and_contract(self):
        profile = self.profiles["github-copilot"]
        block = self.directive["copilot_lite_hygiene_review"]
        self.assertEqual(profile["roles"]["hygiene_review"], block)
        contract = self.contract["supplemental_reviewers"]["github_copilot_lite"]
        self.assertFalse(contract["provider_or_committee_member"])
        self.assertIsNone(contract["workload_percentage"])
        self.assertFalse(contract["gating"])
        self.assertEqual(contract["authority_granted"], [])
        for lane, other in self.profiles.items():
            if lane != "github-copilot":
                self.assertIsNone(other["roles"]["hygiene_review"], lane)


class AntigravityModeTests(unittest.TestCase):
    def test_team_eligibility_is_separate_from_restricted_runtime(self):
        profile = pp.load_profiles()["antigravity"]
        team = profile["execution_modes"]["team"]
        self.assertEqual(team["assignment_operations"], ["engineering", "review"])
        self.assertEqual(team["operational_status"], "not_qualified_not_live")
        self.assertFalse(team["enabled_by_default"])
        self.assertFalse(team["may_self_accept"])
        self.assertFalse(team["may_merge"])
        self.assertTrue(team["review_requires_different_material_author_family"])
        self.assertEqual(profile["capabilities"]["may_issue_review_verdict"], "never")
        self.assertFalse(profile["capabilities"]["receives_repository_context"])
        rendered = pp.render_profile_markdown(profile, pp.load_route_catalog())
        self.assertIn("## Execution modes", rendered)
        self.assertIn("preserved restricted mode", rendered)
        self.assertIn("not_qualified_not_live", rendered)

    def test_mode_contract_rejects_authority_or_qualification_escalation(self):
        original = pp.load_profiles()["antigravity"]
        changes = {"operational_status": "live", "enabled_by_default": True,
                   "may_merge": True, "required_gates": [],
                   "review_requires_different_material_author_family": False,
                   "assignment_operations": ["engineering", "review", "deploy"]}
        for key, value in changes.items():
            with self.subTest(key=key):
                document = json.loads(json.dumps(original))
                document["execution_modes"]["team"][key] = value
                with self.assertRaises(pp.ProviderProfileError):
                    pp.validate_document(document)

    def test_mode_scope_is_required_for_antigravity_only(self):
        profiles = pp.load_profiles()
        agy = json.loads(json.dumps(profiles["antigravity"]))
        modes = agy.pop("execution_modes")
        with self.assertRaises(pp.ProviderProfileError):
            pp.validate_document(agy)
        other = json.loads(json.dumps(profiles["claude"]))
        other["execution_modes"] = modes
        with self.assertRaises(pp.ProviderProfileError):
            pp.validate_document(other)

    def test_restricted_contact_rules_do_not_claim_provider_wide_authority(self):
        profile = pp.load_profiles()["antigravity"]
        strategy = _settings().provider_strategy()["gemini"]
        self.assertEqual(strategy["contact_scope"], "restricted_fixture_and_invited_debate")
        self.assertIn("restricted fixture", profile["roles"]["transport_rule"])
        self.assertIn("Restricted fixture", profile["authority"]["review_requirement"])
        contract = yaml.safe_load(ROLES_CONTRACT.read_text(encoding="utf-8"))
        fixture = contract["providers"]["antigravity"]
        self.assertEqual(fixture["contact_scope"], strategy["contact_scope"])
        self.assertTrue(fixture["mandatory_downstream_verification"]["required"])
        self.assertEqual(fixture["mandatory_downstream_verification"]["assignee"],
                         "independent_non_author_with_verified_integration_capability")
        self.assertFalse(profile["execution_modes"]["team"]["enabled_by_default"])
        self.assertTrue(profile["execution_modes"]["team"]["review_requires_different_material_author_family"])


class ConfigMirrorTests(unittest.TestCase):
    def setUp(self):
        self.profiles = _profiles()
        self.settings = _settings()
        self.env = _env_example()
        self.fields = {field.name for field in dataclasses.fields(Settings)}
        self.primary_sources = "".join(
            (SRC / name).read_text(encoding="utf-8")
            for name in (
                "config.py",
                "primary_default.py",
                "primary_transports.py",
                "host_provider_bridge.py",
                "chatgpt_bridge.py",
                "native_codex_transport.py",
            )
        )

    def test_supplemental_token_prices_are_documented_without_values(self):
        for lane, provider in (("antigravity", "gemini"), ("grok", "grok"), ("perplexity", "perplexity")):
            variables = {item["variable"]: item for item in self.profiles[lane]["budgets_and_limits"]["variables"]}
            for direction in ("input", "output"):
                field = f"{provider}_{direction}_usd_per_mtok"
                with self.subTest(field=field):
                    item = variables[field.upper()]
                    self.assertEqual(item["settings_field"], field)
                    self.assertEqual(item["kind"], "token_price_usd_per_mtok")
                    self.assertIsNone(item["default"])
                    self.assertFalse(item["values_recorded"])
                    self.assertTrue(item["documented_in_env_example"])
                    self.assertEqual(self.env[field.upper()], "")

    def test_credential_allowlists_mirror_transport_maps(self):
        for lane, profile in self.profiles.items():
            credentials = profile["credential_environment"]
            with self.subTest(lane=lane):
                self.assertEqual(credentials["primary_lane_allowlist"], sorted(_PROVIDER_AUTH_ENVIRONMENT[lane]))
                if lane in SUPPORTED_HOST_LANES:
                    self.assertEqual(credentials["host_bridge_allowlist"], sorted(_LANE_AUTH_ENV[lane]))
                else:
                    self.assertIsNone(credentials["host_bridge_allowlist"])
                for field in credentials["settings_credential_fields"]:
                    self.assertIn(field, self.fields)

    def test_host_bridge_transport_mirrors_lane_sets(self):
        for lane, profile in self.profiles.items():
            bridge = [item for item in profile["transports"] if item["transport_id"] == "windows-host-provider-bridge"]
            with self.subTest(lane=lane):
                if lane in SUPPORTED_HOST_LANES:
                    self.assertEqual(len(bridge), 1)
                    self.assertEqual(bridge[0]["status"], "implemented_disabled_by_default")
                elif lane in frozenset():
                    self.assertEqual(len(bridge), 1)
                    self.assertEqual(bridge[0]["status"], "prohibited")
                else:
                    self.assertEqual(bridge, [])
        route = _routes(_catalog())["windows-host-provider-bridge"]
        self.assertEqual(sorted(route["providers_involved"]), sorted(SUPPORTED_HOST_LANES))

    def test_readiness_keys_and_locks_mirror_settings(self):
        functions = {
            "gemini_supplemental_readiness": self.settings.gemini_supplemental_readiness,
            "grok_primary_readiness": self.settings.grok_primary_readiness,
            "perplexity_research_readiness": self.settings.perplexity_research_readiness,
            "readiness": self.settings.readiness,
        }
        must_stay_false = set()
        elevating = set()
        for lane, profile in self.profiles.items():
            gating = profile["gating"]
            with self.subTest(lane=lane):
                if gating["readiness_function"] == "none":
                    self.assertEqual(gating["readiness_keys"], [])
                    self.assertIsNone(gating["health_key"])
                    continue
                readiness = functions[gating["readiness_function"]]()
                if gating["readiness_function"] == "readiness":
                    self.assertTrue(set(gating["readiness_keys"]).issubset(readiness))
                else:
                    self.assertEqual(gating["readiness_keys"], sorted(readiness))
                self.assertIn(gating["health_key"], self.settings.readiness())
                for item in gating["variables"]:
                    if item["readiness_key"] is not None and gating["readiness_function"] != "readiness":
                        self.assertIn(item["readiness_key"], readiness, item["variable"])
                    if item["kind"] == "allow_flag_must_stay_false":
                        must_stay_false.add(item["variable"])
                        self.assertIs(item["required_value"], False)
                        flipped = replace(self.settings, **{item["settings_field"]: True})
                        self.assertFalse(getattr(flipped, gating["readiness_function"])()[item["readiness_key"]], item["variable"])
                    if item["kind"] == "allow_flag_elevating":
                        elevating.add(item["variable"])
                    if item["kind"] == "mode_lock":
                        self.assertEqual(getattr(self.settings, item["settings_field"]), item["required_value"])
                        flipped = replace(self.settings, **{item["settings_field"]: "profile-test"})
                        self.assertFalse(getattr(flipped, gating["readiness_function"])()[item["readiness_key"]], item["variable"])
                    if item["kind"] == "endpoint_lock" and item["readiness_key"] is not None:
                        self.assertEqual(getattr(self.settings, item["settings_field"]), item["required_value"])
                        flipped = replace(self.settings, **{item["settings_field"]: "https://profile-test.invalid"})
                        self.assertFalse(getattr(flipped, gating["readiness_function"])()[item["readiness_key"]], item["variable"])
        self.assertEqual(
            must_stay_false,
            {"GROK_ALLOW_TOOLS", "GEMINI_ALLOW_REVIEW_VERDICT", "GEMINI_ALLOW_PRIMARY_AUTHORITY", "PERPLEXITY_ALLOW_REPOSITORY_WRITE", "PERPLEXITY_ALLOW_REVIEW_VERDICT"},
        )
        self.assertEqual(elevating, {"GROK_ALLOW_REPOSITORY_WRITE", "GROK_ALLOW_REVIEW_VERDICT"})

    def test_config_names_and_defaults_mirror_settings_and_env_example(self):
        for lane, profile in self.profiles.items():
            for item in _config_variables(profile):
                name = item["variable"]
                with self.subTest(lane=lane, variable=name):
                    self.assertEqual(item["documented_in_env_example"], name in self.env, name)
                    self.assertIs(item["values_recorded"], False)
                    field = item["settings_field"]
                    if field is None:
                        self.assertIsNone(item["default"])
                        self.assertIn(f'"{name}"', self.primary_sources, name)
                        continue
                    self.assertIn(field, self.fields, name)
                    actual = getattr(self.settings, field)
                    expected = item["default"]
                    if isinstance(expected, bool) or isinstance(actual, bool):
                        self.assertEqual(actual, expected)
                    elif isinstance(expected, (int, float)):
                        self.assertAlmostEqual(float(actual), float(expected))
                    elif item.get("kind") == "repository_map":
                        self.assertEqual(json.loads(expected), dict(actual))
                    elif isinstance(actual, (list, tuple)):
                        self.assertEqual(json.loads(expected), list(actual))
                    else:
                        self.assertEqual(str(actual), str(expected))

    def test_every_env_example_key_with_a_profile_prefix_is_named_by_that_profile(self):
        for lane, profile in self.profiles.items():
            names = _yaml_names((PROFILE_ROOT / f"{lane}.yaml").read_text(encoding="utf-8"))
            for prefix in profile["identifiers"]["config_prefixes"]:
                for key in self.env:
                    if key.startswith(prefix):
                        with self.subTest(lane=lane, key=key):
                            self.assertIn(key, names)

    def test_spend_ledger_membership_mirrors_usage_monitor(self):
        supported = {_enum_value(item) for item in ProviderDailySpendLedger._SUPPORTED}
        for lane, profile in self.profiles.items():
            with self.subTest(lane=lane):
                self.assertEqual(
                    profile["budgets_and_limits"]["spend_ledger_member"],
                    profile["identifiers"]["provider_name_enum"] in supported,
                )

    def test_transport_enum_values_are_each_claimed_once(self):
        claimed = {}
        for lane, profile in self.profiles.items():
            for item in profile["transports"]:
                if item["enum_value"] is not None:
                    self.assertNotIn(item["enum_value"], claimed, item["enum_value"])
                    claimed[item["enum_value"]] = lane
        unclaimed = {"director_backend", "slack", "manual_owner", "simulation"}
        self.assertEqual(set(claimed) | unclaimed, {item.value for item in ProviderTransport})
        self.assertNotIn("grok_bot", {item.value for item in ProviderTransport})

    def test_public_research_lanes_reject_effects(self):
        rejecting = {
            lane
            for lane, profile in self.profiles.items()
            if profile["authority"]["external_effects"]["public_research_lane_rejects_all_effects"]
        }
        self.assertEqual(rejecting, set(_PRIMARY_PUBLIC_RESEARCH_LANES))
        for lane in rejecting:
            self.assertEqual(self.profiles[lane]["authority"]["external_effects"]["may_be_granted"], [])
            search = [item for item in self.profiles[lane]["transports"] if item["kind"] == "public_search_cli"]
            self.assertEqual(len(search), 1, lane)

    def test_grok_bot_is_documented_honestly(self):
        grok_bot = next(item for item in self.profiles["grok"]["transports"] if item["transport_id"] == "grok-bot")
        self.assertEqual(grok_bot["status"], "proposed")
        self.assertIsNone(grok_bot["enum_value"])
        self.assertEqual(grok_bot["config"], [])


class RouteCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = _catalog()
        self.routes = _routes(self.catalog)
        self.profiles = _profiles()

    def test_explicit_membership_does_not_expand_through_wildcard(self):
        route = {"providers_involved": ["openai"],
                 "endpoints": {"from": "director", "to": "all_providers"}}
        self.assertTrue(pp._route_involves(route, "openai"))
        self.assertFalse(pp._route_involves(route, "perplexity"))
        route["providers_involved"] = []
        self.assertTrue(pp._route_involves(route, "perplexity"))

    def test_security_route_cannot_borrow_workflow_skills_exemption(self):
        self.routes["codex-security-review-comment"]["enforced_by"] = []
        problems = pp.cross_validate(self.profiles, self.catalog)
        self.assertIn("codex-security-review-comment: authorized route requires an enforcer", problems)

    def test_rendered_names_cannot_be_agent_instruction_files(self):
        outputs = pp.planned_outputs(None)
        for lane in LANES:
            self.assertIn(PROFILE_ROOT / f"{lane}.profile.md", outputs)
            self.assertFalse((PROFILE_ROOT / f"{lane}.md").exists())
        index = outputs[PROFILE_ROOT / pp.INDEX_FILENAME]
        self.assertIn("antigravity.profile.md#execution-modes", index)

    def test_status_rules(self):
        ids = [route["route_id"] for route in self.catalog["routes"]]
        self.assertEqual(len(ids), len(set(ids)))
        proposed = set()
        prohibited = set()
        for route_id, route in self.routes.items():
            with self.subTest(route=route_id):
                status = route["status"]
                if status == "authorized":
                    if route_id != "shared-workflow-skills":
                        self.assertTrue(route["enforced_by"])
                    self.assertIsNone(route["prohibition_quote"])
                elif status == "proposed":
                    proposed.add(route_id)
                    self.assertEqual(route["enforced_by"], [])
                    self.assertIn(route["default_config_state"], {"blocked", "not_applicable"})
                else:
                    prohibited.add(route_id)
                    self.assertTrue(route["prohibition_quote"])
                    self.assertEqual(route["default_config_state"], "not_applicable")
                for reference in route["charter_basis"]:
                    text = (ROOT / reference["document"]).read_text(encoding="utf-8")
                    self.assertIn(reference["anchor"], text, route_id)
                for code_ref in route["enforced_by"]:
                    self.assertTrue((ROOT / code_ref.split(":", 1)[0]).is_file(), code_ref)
        self.assertEqual(proposed, PROPOSED_ROUTES)
        self.assertEqual(prohibited, {pp.PEER_RULE_ROUTE_ID})

    def test_peer_rule_is_verbatim_and_total(self):
        rule = self.routes[pp.PEER_RULE_ROUTE_ID]
        self.assertEqual(rule["kind"], "direct")
        self.assertEqual(sorted(rule["providers_involved"]), sorted(LANES))
        anchors = {reference["anchor"] for reference in rule["charter_basis"]}
        self.assertIn("They do not post directly or converse forever.", anchors)
        self.assertIn("Agents do not address each other through uncontrolled chat loops.", anchors)
        self.assertIn("provider_messages_relay_through_director: true", anchors)
        basis = {reference["anchor"] for reference in self.catalog["provider_to_provider_rule"]["basis"]}
        self.assertTrue(anchors.issubset(basis) or basis.issubset(anchors))
        for lane in LANES:
            for other in LANES:
                if lane == other:
                    continue
                status, direct, mediated = pp.peer_status(lane, other, self.catalog)
                with self.subTest(lane=lane, other=other):
                    self.assertEqual(status, "prohibited")
                    self.assertEqual(direct, [pp.PEER_RULE_ROUTE_ID])
                    self.assertTrue(mediated)

    def test_mediated_routes_cover_every_lane(self):
        for route_id in ("work-packet-dispatch", "result-packet-return", "slack-collaboration-relay", "dependency-outcome-forwarding", "owner-manual-recovery"):
            self.assertEqual(sorted(self.routes[route_id]["providers_involved"]), sorted(LANES), route_id)
        self.assertTrue({"claude", "cursor", "grok"}.issubset(self.routes["review-to-merge-handoff"]["providers_involved"]))
        self.assertEqual(sorted(self.routes["advisory-debate"]["providers_involved"]), sorted(DEBATE_SEATS))
        for route_id in ("antigravity-invited-debate", "antigravity-closed-work-directive"):
            self.assertEqual(self.routes[route_id]["providers_involved"], ["antigravity"])

    def test_every_transport_route_exists_and_maps_status(self):
        for lane, profile in self.profiles.items():
            for item in profile["transports"]:
                with self.subTest(lane=lane, transport=item["transport_id"]):
                    self.assertIn(item["route_id"], self.routes)
                    route = self.routes[item["route_id"]]
                    if item["status"] in {"implemented", "implemented_disabled_by_default"} and lane in route["providers_involved"]:
                        self.assertEqual(route["status"], "authorized")
                    if item["status"] in {"proposed", "blocked_pending_evidence"} and lane in route["providers_involved"]:
                        self.assertEqual(route["status"], "proposed")


class DescriptiveOnlyTests(unittest.TestCase):
    def setUp(self):
        self.profiles = _profiles()
        self.contract = yaml.safe_load(ROLES_CONTRACT.read_text(encoding="utf-8"))

    def test_constants_declare_no_authority(self):
        for lane, profile in self.profiles.items():
            with self.subTest(lane=lane):
                self.assertEqual(profile["authority_granted"], "none")
                self.assertEqual(profile["this_profile_does_not_change"], self.contract["this_template_does_not_change"])
                self.assertFalse(profile["authority"]["self_review"])
                self.assertFalse(profile["capabilities"]["may_merge"])
                self.assertFalse(profile["capabilities"]["may_deploy_or_publish"])
                self.assertFalse(profile["capabilities"]["may_post_to_slack"])
                self.assertFalse(profile["communication"]["direct_provider_to_provider_channels_exist"])
                for value in _walk_keys(profile, "values_recorded"):
                    self.assertIs(value, False)
        self.assertEqual(_catalog()["authority_granted"], "none")

    def test_runtime_never_imports_the_profile_module(self):
        for path in SRC.glob("*.py"):
            if path.name == "provider_profiles.py":
                continue
            self.assertNotIn("provider_profiles", path.read_text(encoding="utf-8"), path.name)


class HygieneTests(unittest.TestCase):
    def test_sensitive_field_classification_preserves_credentials_and_excludes_limits(self):
        for name in (
            "OPENAI_API_KEY", "SLACK_BOT_TOKEN", "ORCHESTRATOR_SHARED_SECRET",
            "ENTITLEMENT_POSTGRES_DSN_FILE", "DIRECTOR_HOST_PROVIDER_TLS_KEY_FILE",
            "LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON",
            "SLACK_OWNER_USER_ID",
            "CHATGPT_BRIDGE_CHANNEL_IDS",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(SECRET_NAME.search(name))
        for name in ("GEMINI_MAX_TOTAL_TOKENS", "USAGE_CHECK_MAX_OUTPUT_TOKENS", "TOKEN_BUCKET_SIZE"):
            with self.subTest(name=name):
                self.assertIsNone(SECRET_NAME.search(name))

    def test_no_values_credentials_or_identifiers(self):
        env = _env_example()
        secret_values = {value for name, value in env.items() if value and SECRET_NAME.search(name)}
        for path in sorted(PROFILE_ROOT.glob("*")) + [SCHEMA_PATH, EXAMPLE_PATH]:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertTrue(text.isascii())
                self.assertNotIn("\r", text)
                self.assertNotIn("REPLACE_WITH", text)
                self.assertIsNone(re.search(r"\b[UC]0[A-Z0-9]{8,}\b", text))
                self.assertIsNone(re.search(r"gh[oprsu]_[A-Za-z0-9_]{30,}|sk-ant-|sk-[A-Za-z0-9_-]{30,}|xox[baprs]-", text))
                for value in secret_values:
                    self.assertNotIn(value, text)
        for lane, profile in _profiles().items():
            for value in _walk_strings(profile):
                self.assertNotIn("\n", value, lane)


class RenderTests(unittest.TestCase):
    def test_scalar_description_is_one_complete_bullet(self):
        self.assertEqual(pp._bullets("Bounded artifacts from a provider"), ["- Bounded artifacts from a provider"])
        self.assertEqual(pp._bullets(["first", "second"]), ["- first", "- second"])

    def test_rendered_outputs_are_current_and_tagged(self):
        self.assertEqual(pp.check_outputs(), [])
        for path in list(PROFILE_ROOT.glob("*.md")) + list(PROFILE_ROOT.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("\r", text)
                relative = path.relative_to(ROOT).as_posix()
                self.assertEqual(validate_inline_contribution_tags(relative, text), [])
                self.assertIn(pp.GENERATED_MARKER.split("{")[0], text if path.suffix == ".md" else pp.GENERATED_MARKER)

    def test_compose_tagged_document_is_idempotent_for_the_same_identity(self):
        identity = pp.TagIdentity("Claude Code", "Claude Fable 5.1", datetime(2026, 9, 6, 19, 40, tzinfo=timezone.utc))
        body = "# Sheet\n\nbody\n"
        first = pp.compose_tagged_document(body, None, identity)
        again = pp.compose_tagged_document(body, first, identity)
        self.assertEqual(first, again)
        later = pp.TagIdentity("Claude Code", "Claude Fable 5.1", datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc))
        wrapped = pp.compose_tagged_document(body, first, later)
        self.assertEqual(sum(1 for line in wrapped.splitlines() if parse_contribution_tag(line)), 4)
        self.assertEqual(pp.strip_contribution_tag_lines(wrapped), body)


class DiscoveryTests(unittest.TestCase):
    def test_agents_md_section_is_present_and_ordered(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        section = text.split("\n## Provider profile sheets", 1)[1]
        self.assertLess(text.index("## LangGraph transition work"), text.index("## Provider profile sheets"))
        self.assertLess(text.index("## Postgres-backed unit tests"), text.index("## Cursor Cloud specific instructions"))
        for phrase in (
            "docs/provider-profiles/",
            "grant no authority",
            "documenting a route never authorizes it",
            "authorized | prohibited | proposed",
            "No direct provider-to-provider channel exists",
            "scripts/render_provider_profiles.py",
            "tests.test_provider_profiles",
        ):
            self.assertIn(phrase, section, phrase)
        claude_instructions = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("@AGENTS.md", claude_instructions.splitlines())
        self.assertIn("no standing director role", claude_instructions.lower())
        self.assertNotIn("Lead delivery:", claude_instructions)
        self.assertIn("requires explicit assignment authority", claude_instructions)
        self.assertIn("docs/project-leadership.md", claude_instructions)

    def test_active_surfaces_avoid_non_exempt_standing_role_language(self):
        files = (
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / "README.md",
            ROOT / "docs" / "project-leadership.md",
            ROOT / "docs" / "provider-allocation-profiles.md",
            ROOT / "docs" / "contribution-attribution-policy.md",
            ROOT / "docs" / "team-operating-charter.md",
            ROOT / "prompts" / "director-system.md",
            ROOT / "docs" / "templates" / "project-bootstrap" / "provider-roles-contract.yaml",
            *sorted((ROOT / "docs" / "provider-profiles").glob("*.yaml")),
            *sorted((ROOT / "docs" / "provider-profiles").glob("*.md")),
        )
        banned = (
            "project secretary",
            "subordinate supplemental specialist",
            "cursor-family primary teammate",
            "supplemental hygiene reviewer",
            "acting director-orchestrator",
            "claude is the acting project director",
            "claude's direction",
            "claude project leadership",
            "claude acting leadership",
            "staged policy target",
            "does not retain project leadership after the claude handoff",
            "acting claude director",
            "claude leads project coordination",
            "claude-led",
            "claude keeps moderator, participant, and director project functions separate",
        )
        for path in files:
            body = path.read_text(encoding="utf-8").lower()
            with self.subTest(path=path.name):
                for phrase in banned:
                    self.assertNotIn(phrase, body)

    def test_director_prompt_readme_and_provider_setup_point_at_the_sheets(self):
        prompt = (ROOT / "prompts" / "director-system.md").read_text(encoding="utf-8")
        self.assertIn("docs/provider-profiles/", prompt)
        self.assertIn("grant no authority", prompt)
        self.assertIn("No direct provider-to-provider channel exists; route every exchange through the Director.", prompt)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/provider-profiles/README.md", readme)
        local = readme.split("\n## Local validation", 1)[1]
        self.assertIn("scripts\\render_provider_profiles.py --check", local)
        setup = (ROOT / "docs" / "provider-setup.md").read_text(encoding="utf-8")
        self.assertIn("docs/provider-profiles/", setup)
        self.assertIn("grant no authority", setup)


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Fable 5.1 | 2026-09-06 | 19:40 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 18:54 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:34 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:50 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:04 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 06:51 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 17:55 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:21 UTC | END]]

# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 09:59 UTC | END]]
# [[OpenAI Codex queue_runtime author | GPT-6 Astra | 2026-09-12 | 10:07 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 23:46 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 03:10 UTC | END]]
# [[OpenAI Codex integration | GPT-6 Astra | 2026-09-13 | 03:32 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 04:13 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:55 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 04:50 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 05:30 UTC | END]]
