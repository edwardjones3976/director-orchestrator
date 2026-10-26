"""Readable provider guidance must not reproduce admission policy data."""

import ast
import hashlib
import inspect
import json
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

import yaml
from jsonschema import Draft202012Validator

from director_orchestrator import _retry_constants, allocation_profiles, provider_profiles, workload_policy
from director_orchestrator.director import OpenAIDirector
from director_orchestrator.team_charter import team_operating_charter


ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "mixed": (
        "6f9be5429835eed4103423c993a164c6a5715a2f025b304836496b448121d35f",
        "sha256:f7a925d492c220cff9b943c1a509722868b16261389dcd2d4b84d7ad35bda866",
        "sha256:f228e056553673abdffce8f84af45801deb73e0b2bd612930e07fcc36980e976",
        "sha256:29e6c0aaef011c0c42a45e883ecc345af726bb6776b5aa09a1fdc65559b6f414",
    ),
    "engineering": (
        "c0bb25d2d70043e2868dc218231ee62c9e5fe1617256731accf70f2cb84e6cb6",
        "sha256:d94f3a6e3007193d596f9ac38ff2fa3c7e0da9c26e9f0ce338d4c7d9f4c5e279",
        "sha256:1928779a3d24e9172a8f74911b601bc01905dc2dbfd49c306f0dcff73e442a68",
        "sha256:d6e1b1845fdbe0ea0668362b90ad61951b5b1ba3dd12ebdd1e39d12cc4dbb0ff",
    ),
    "public_research": (
        "c11d3c060c4919b63a7bef66a58ed5fd079cff47b0968b3978f00b5a51f4ed47",
        "sha256:9e1e3e6cb0c0f5d9e3a61b570274ca1fff95a6c5f6c93f1a08732aeee5979296",
        "sha256:81687a465f4fcbae938733dc83cddc2ad293e519983f4f9c79394f41a9d9474c",
        "sha256:2c468e15e80c666cf7d638397c390a30acb9621dc58f10e6a31165e1a988b609",
    ),
    "enterprise": (
        "a9cbb74863eb18dcae78311c46367ddeb8ba3f6729c418fcf4c92ed2c607bd0b",
        "sha256:05853f2b553283dc8d21de6fdc4b99a6ed80afa5eca41ed414a738b700d85764",
        "sha256:db08376ea3c632828f326d6e08f4ead2a865bd8fbb7faa1396969275c5a8815a",
        "sha256:f13e3660a3ca511e780fbdee01a872ab730790f1e712e7ffacd266d5641902a2",
    ),
}
PRESENTATION_FIELDS = (
    "allocation_profiles", "allocation_targets", "default_allocation_profile",
    "allocation_module", "direct_workload_percent", "overall_effort_percent",
    "workload_enforcement", "workload_percentage", "effort_band_percent",
)

# These exact non-numeric tables describe runtime identities, never target shares.
RUNTIME_IDENTITY_TABLES = {
    ("docs/engineering-workload-ledger.html", ("product id", "canonical allocation bucket", "identity constraint")):
        "Runtime product-to-lane identities; no percentages or target values.",
    ("docs/grok-lanes-separation-2026-09-09.html", ("name", "what it actually is", "whose quota it spends", "present in this repository")):
        "Transport funding and identity boundaries; no allocation targets.",
}
RUNTIME_RECORDS = {
    "docs/examples/offline-slack-trial-observations-v1.json": "Existing serialized trial input retains its intake field names.",
    "docs/schemas/director-slack-trial-observations-v1.example.json": "Frozen trial-record schema example retains its intake field names.",
}
PROVIDER = r"\b(?:openai|codex|claude|cursor|(?:direct\s+)?grok|gemini|antigravity|perplexity|firecrawl|(?:github\s+)?copilot|z\.ai|provider_[a-z]+)\b"
PERCENT = r"(?:\d+(?:\.\d+)?|NN)\s*%"
ALLOCATION_TERM = r"\b(?:allocation|bucket|targets?|shares?|ceilings?|quotas?|budget)\b"
RUNTIME_ALIAS = "Gemini and Antigravity share one lane."
RUNTIME_ALIAS_PATHS = {
    "docs/provider-profiles/antigravity.yaml": "The verified Gemini-to-Antigravity runtime alias is not an allocation share.",
    "docs/provider-profiles/antigravity.profile.html": "Generated presentation of the same runtime alias; no target exception.",
}


class _DocumentText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocks = []
        self.pending = []

    def _flush(self):
        block = " ".join(" ".join(self.pending).split())
        if block:
            self.blocks.append(block)
        self.pending.clear()

    def handle_data(self, data):
        self.parts.append(data)
        self.pending.append(data)

    def handle_comment(self, data):
        self._flush()
        self.parts.append(data)
        self.blocks.append(" ".join(data.split()))

    def handle_starttag(self, tag, attrs):
        if tag in {"td", "th", "li", "p", "dt", "dd", "div", "section", "pre", "script", "template"} or re.fullmatch(r"h[1-6]", tag):
            self._flush()
        for key, value in attrs:
            if key.startswith("data-") and value:
                self.parts.append(value)
                self.blocks.append(" ".join(value.split()))

    def handle_endtag(self, tag):
        if tag in {"td", "th", "li", "p", "dt", "dd", "div", "section", "pre", "script", "template"} or re.fullmatch(r"h[1-6]", tag):
            self._flush()

    def close(self):
        super().close()
        self._flush()


def _yaml_scalars(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _yaml_scalars(child)
    elif isinstance(value, list):
        for child in value:
            yield from _yaml_scalars(child)
    elif isinstance(value, str):
        yield " ".join(value.split())


def _allocation_copy(text, path=""):
    for _ in range(3):
        text = unescape(text)
    for table in re.findall(r"<table\b[^>]*>.*?</table>", text, re.S | re.I):
        headers = tuple(" ".join(re.sub(r"<[^>]*>", " ", header).lower().split())
                        for header in re.findall(r"<th\b[^>]*>(.*?)</th>", table, re.S | re.I))
        percentage = re.search(PERCENT, table, re.I)
        if (path, headers) in RUNTIME_IDENTITY_TABLES and not percentage:
            continue
        if any(re.search(ALLOCATION_TERM, header, re.I) for header in headers) and (
            percentage or re.search(PROVIDER, table, re.I)
        ):
            return "allocation table"
    parser = _DocumentText()
    parser.feed(text)
    parser.close()
    flattened = " ".join(" ".join(parser.parts).split())
    if re.search(PROVIDER + r"[\s:=\"'|,-]*" + PERCENT, flattened, re.I) or re.search(
        PERCENT + r"[\s:=\"'|,-]*" + PROVIDER, flattened, re.I
    ):
        return "provider percentage"
    if re.search(r'["\'](?:allocation_profiles|weights|targets|provider_percentages)["\']\s*:\s*[{\[]', flattened, re.I) and re.search(PROVIDER, flattened, re.I):
        return "inline allocation data"
    blocks = list(_yaml_scalars(yaml.safe_load(text))) if path.endswith((".yaml", ".yml")) else parser.blocks
    alias_count = sum(block.count(RUNTIME_ALIAS) for block in blocks)
    if path in RUNTIME_ALIAS_PATHS and alias_count != 1:
        return "runtime alias missing or duplicated"
    if path not in RUNTIME_ALIAS_PATHS and alias_count:
        return "runtime alias outside its named paths"
    for block in blocks:
        if RUNTIME_ALIAS in block:
            block = block.replace(RUNTIME_ALIAS, "", 1)
            if re.search(PERCENT, block, re.I) or re.search(r"\bweights?\b", block, re.I):
                return "allocation appended to runtime alias"
        has_data = re.search(r"\b\d+(?:\.\d+)?\b|" + PERCENT + r"|\bweights?\b", block, re.I)
        if path not in RUNTIME_RECORDS and has_data and re.search(PROVIDER + r"\s+" + ALLOCATION_TERM, block, re.I):
            return "provider allocation text"
    return None


class ProviderPresentationBoundaryTests(unittest.TestCase):
    def test_canonical_current_and_historical_pins_match_base(self):
        for profile, pins in PINS.items():
            with self.subTest(profile=profile):
                targets = json.dumps(dict(allocation_profiles.allocation_targets(profile)), sort_keys=True, separators=(",", ":")).encode()
                self.assertEqual(hashlib.sha256(targets).hexdigest(), pins[0])
                self.assertEqual(allocation_profiles.allocation_profile_digest(profile), pins[1])
                self.assertEqual(allocation_profiles.historical_allocation_profile_digest(profile), pins[2])
                self.assertEqual(allocation_profiles.pre_zai_allocation_profile_digest(profile), pins[3])

    def test_retry_reexports_preserve_values_and_identity(self):
        for name, expected in {
            "MAX_PROVIDER_RETRIES": 4, "MAX_PROVIDER_ATTEMPTS": 5,
            "LEGACY_PROVIDER_ATTEMPTS": 3, "SUPPORTED_ATTEMPT_LIMITS": frozenset({3, 5}),
        }.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(workload_policy, name), expected)
                self.assertIs(getattr(workload_policy, name), getattr(_retry_constants, name))

    def test_renderers_have_no_transitive_admission_dependency(self):
        graph = {}
        for path in (ROOT / "src/director_orchestrator").glob("*.py"):
            graph[path.stem] = {
                node.module.split(".")[0]
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
            }
        script = ROOT / "scripts/render_provider_profiles.py"
        graph["render_provider_profiles"] = {
            node.module.split(".")[1]
            for node in ast.walk(ast.parse(script.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("director_orchestrator.")
        }
        for start in ("team_charter", "provider_profiles", "render_provider_profiles"):
            pending = [start]
            visited = set()
            while pending:
                module = pending.pop()
                if module in visited:
                    continue
                visited.add(module)
                pending.extend(graph.get(module, ()))
            self.assertFalse(visited & {"allocation_profiles", "workload_policy", "primary_workload", "engineering_workload_ledger"}, start)
        self.assertNotIn("allocation", inspect.getsource(OpenAIDirector.plan))
        self.assertFalse(any("allocation" in name for name in vars(provider_profiles)))

    def test_descriptive_schema_rejects_removed_fields(self):
        schema = json.loads((ROOT / "docs/schemas/director-provider-profile-v1.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for profile in provider_profiles.load_profiles().values():
            self.assertTrue(validator.is_valid(profile))
            for container, field in (("roles", "allocation"), ("source_of_truth", "allocation_module")):
                candidate = json.loads(json.dumps(profile))
                candidate[container][field] = {}
                self.assertFalse(validator.is_valid(candidate), (profile["provider_id"], field))

    def test_structured_views_and_prompts_exclude_admission_fields(self):
        surfaces = {
            "charter": json.dumps(team_operating_charter()),
            "prompt": (ROOT / "prompts/director-system.md").read_text(encoding="utf-8"),
            "bootstrap": (ROOT / "docs/templates/project-bootstrap/provider-roles-contract.yaml").read_text(encoding="utf-8"),
            "schema": (ROOT / "docs/schemas/director-provider-profile-v1.schema.json").read_text(encoding="utf-8"),
            "example": (ROOT / "docs/schemas/director-provider-profile-v1.example.json").read_text(encoding="utf-8"),
        }
        for path in (ROOT / "docs/provider-profiles").iterdir():
            if path.suffix in {".yaml", ".html"}:
                surfaces[path.name] = path.read_text(encoding="utf-8")
        for name, text in surfaces.items():
            for field in PRESENTATION_FIELDS:
                self.assertFalse(field in text, (name, field))

    def test_docs_contain_no_matrix_or_hidden_copies(self):
        paths = [ROOT / "AGENTS.md", *sorted(ROOT.glob("README*")),
                 *(path for path in sorted((ROOT / "docs").rglob("*"))
                   if path.suffix.lower() in {".html", ".md", ".yaml", ".yml", ".json", ".txt"})]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for marker in ("provider-allocation-profiles", "allocation_profiles.py", "allocation_profiles.LANES", "direct_workload_percent", "overall_effort_percent"):
                self.assertFalse(marker in text, (str(path.relative_to(ROOT)), marker))
            relative = path.relative_to(ROOT).as_posix()
            self.assertIsNone(_allocation_copy(text, relative), relative)

    def test_generic_and_concealed_target_copies_are_rejected(self):
        table = """<table><tr><th>Allocation bucket</th><th>Target</th>
        <th>Normal full-window count</th><th>Retired hard ceiling</th></tr>
        <tr><td>PROVIDER_A</td><td>NN%</td><td>NN</td><td>NN</td></tr></table>"""
        for copy in (table, table.swapcase(), "<!--" + table + "-->",
                     '<div hidden>' + table + '</div>',
                     '<div style="display:none" aria-hidden="true">' + table + '</div>',
                     table.replace("%", "&#37;"), table.replace("%", "&percnt;"),
                     table.replace("<", "&lt;").replace(">", "&gt;"),
                     '<div data-policy="PROVIDER_A NN&#37;"></div>',
                     '<script type="application/json">{"targets":{"PROVIDER_A":"NN%"}}</script>',
                     '<template>PROVIDER_A NN%</template>'):
            with self.subTest(copy=copy):
                self.assertIsNotNone(_allocation_copy(copy))

    def test_block_and_runtime_exceptions_cannot_hide_targets(self):
        for copy in ("<p>Cursor</p><p>Target</p>",
                     "<table><tr><td>Cursor</td><td>Target</td></tr></table>",
                     "<p>Cursor quota or outage</p>"):
            self.assertIsNone(_allocation_copy(copy))
        for copy in ("<p>Cursor target NN%</p>", "<p>Cursor quota NN%</p>",
                     '<p>Cursor quota with a weight field</p>'):
            self.assertIsNotNone(_allocation_copy(copy))
        for path in RUNTIME_ALIAS_PATHS:
            self.assertIsNone(_allocation_copy(RUNTIME_ALIAS, path))
            for copy in ("", RUNTIME_ALIAS + RUNTIME_ALIAS, RUNTIME_ALIAS + " NN%",
                         RUNTIME_ALIAS + ' {"weight":"NN"}'):
                self.assertIsNotNone(_allocation_copy(copy, path))
        self.assertIsNotNone(_allocation_copy(RUNTIME_ALIAS, "docs/other.html"))
        for (path, headers) in RUNTIME_IDENTITY_TABLES:
            heading = "".join("<th>" + header + "</th>" for header in headers)
            table = "<table><tr>" + heading + "</tr><tr><td>PROVIDER_A</td></tr></table>"
            self.assertIsNone(_allocation_copy(table, path))
            self.assertIsNotNone(_allocation_copy(table.replace("</td>", " NN%</td>"), path))
            self.assertIsNotNone(_allocation_copy(table + '<table><tr><th>Target</th></tr><tr><td>PROVIDER_A NN%</td></tr></table>', path))
