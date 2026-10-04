"""Standalone HTML presentation of the existing, launcher-attested scorecard JSON.

HTML is a human display, never evidence or authority. The saved JSON and the
owner-installed launcher's completed receipt remain the verification interface.
Legacy Markdown receipts are not rewritten by this renderer.
"""
from __future__ import annotations

from html import escape
import importlib.util
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_ROOT = Path(__file__).resolve().parents[2]
# Inline styles keep the exported report readable offline, outside the checkout.
_CSS = """
body{font:1rem/1.6 system-ui,sans-serif;max-width:80rem;margin:auto;padding:1.5rem;color:#182536;background:#fff}
a{color:#124b9b}a:focus-visible{outline:3px solid #a65300}h1,h2{line-height:1.2}section{margin:2.5rem 0}
dt{font-weight:650;margin-top:.75rem}dd{margin-left:1.25rem}code,dd,p,td{overflow-wrap:anywhere}
.notice{border-left:.35rem solid #a65300;background:#fff6e5;padding:1rem}.summary{font-size:1.35rem;font-weight:700}
.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccd6e0;padding:.6rem;text-align:left;vertical-align:top}
th{background:#eef3f8}caption{text-align:left;font-weight:650;padding:.5rem 0}.state{font-weight:700}
.pass{color:#18552b}.fail{color:#912626}.unknown,.unattested{color:#694400}pre{white-space:pre-wrap;overflow-wrap:anywhere}
nav ul{display:flex;flex-wrap:wrap;gap:1.5rem;list-style:none;padding:0}footer{border-top:1px solid #ccd6e0;margin-top:2rem}
@media print{body{max-width:none;padding:0}nav,.skip{display:none}tr,section>h2{break-inside:avoid}a{color:inherit}}
""".strip()


def _launcher():
    # Delegate all trust decisions to the existing launcher; do not accept an
    # attested JSON boolean or invent an HTML-specific signature/trust root.
    spec = importlib.util.spec_from_file_location("scorecard_launcher", _ROOT / "scripts" / "scorecard-launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _source(value: Any) -> str:
    """Link only explicit HTTP(S) URLs; local paths and other schemes stay text.

    A path display must not cause a browser to contact a UNC host, execute a
    javascript/data URL, or follow credentials embedded in an evidence string.
    """
    source = str(value)
    try:
        url = urlsplit(source)
        safe = (source == source.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in source)
                and "\\" not in source and url.scheme in {"http", "https"} and bool(url.hostname)
                and url.username is None and url.password is None)
        if safe:
            # Accessing port validates malformed port strings and out-of-range values.
            url.port
            return f'<a href="{_text(source)}" rel="noreferrer noopener">{_text(source)}</a>'
    except ValueError:
        pass
    return f"<code>{_text(source)}</code>"


def _pairs(values: list[tuple[str, Any]]) -> str:
    return "<dl>" + "".join(f"<dt>{_text(label)}</dt><dd>{_text(value)}</dd>" for label, value in values) + "</dl>"


def render_html(report: dict[str, Any]) -> str:
    """Render directly from JSON, showing totals only for a completed receipt.

    A pending launch has not yet been sealed and deliberately renders the same
    no-total warning as other unattested reports. The launcher renders again
    after it seals the exact JSON. A copied HTML page proves nothing by itself.
    """
    launcher = _launcher()
    try:
        attested = launcher.report_is_attested(report)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        attested = False
    rows = report.get("checks", [])
    title = "Director completion scorecard"
    if attested:
        summary = f"{_text(report['passed'])}/20 verified ({_text(report['percent'])}%)"
        origin = "Attested: true — the completed JSON receipt was verified at rendering."
    else:
        summary = "Counts: UNATTESTED"
        origin = launcher.UNATTESTED
    parts = [
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
        f"<title>{title}</title><style>{_CSS}</style></head><body>",
        '<a class="skip" href="#main">Skip to content</a>',
        f"<header><h1>{title}</h1><p class=\"summary\">{summary}</p>",
        f'<p class="notice">{_text(origin)}</p>',
        '<p>This is a progress display. It grants no activation, spending or merge authority. '
        'HTML is not evidence: verify the completed JSON receipt with the owner-installed launcher.</p></header>',
        '<nav aria-label="On this page"><ul><li><a href="#identity">Report identity</a></li>'
        '<li><a href="#checks">Checks</a></li><li><a href="#evidence">Evidence</a></li>'
        '<li><a href="#references">Unverified references</a></li><li><a href="#limits">Limits</a></li></ul></nav>',
        '<main id="main"><section aria-labelledby="identity"><h2 id="identity">Report identity</h2>',
        _pairs([("Observed", report.get("generated_at", "UNKNOWN")),
                ("Candidate", report.get("candidate_sha", "UNKNOWN")),
                ("Runtime", report.get("runtime_sha") or "UNKNOWN"),
                ("Image", report.get("image_id") or "UNKNOWN"),
                ("Report schema", report.get("schema", "UNKNOWN")),
                ("Activation", "NOT AUTHORIZED BY SCORECARD")]),
    ]
    if attested:
        parts.append("<details><summary>Launcher attestation</summary><pre>" +
                     _text(json.dumps(report["attestation"], sort_keys=True, indent=2)) + "</pre></details>")
    parts += ['</section><section aria-labelledby="checks"><h2 id="checks">Checks</h2>',
              '<div class="table-scroll"><table><caption>Required outcomes and evidence or next action</caption>'
              '<thead><tr><th scope="col">#</th><th scope="col">Required outcome</th>'
              '<th scope="col">State</th><th scope="col">Evidence or next action</th></tr></thead><tbody>']
    for index, row in enumerate(rows, 1):
        # Do not decorate attacker-supplied PASS values as verified outcomes.
        status = row.get("status", "UNKNOWN") if attested else "UNATTESTED"
        if status not in {"PASS", "FAIL", "UNKNOWN", "UNATTESTED"}:
            status = "UNKNOWN"
        detail = _text(row.get("explanation", "No explanation recorded."))
        if status != "PASS" and row.get("action"):
            detail += "<p><strong>Next:</strong> " + _text(row["action"]) + "</p>"
        parts.append(f'<tr id="check-{index:02d}"><th scope="row">{_text(row.get("id", index))}</th>'
                     f'<td>{_text(row.get("title", "Unknown outcome"))}</td>'
                     f'<td class="state {status.lower()}">{status}</td><td>{detail}</td></tr>')
    parts += ['</tbody></table></div></section><section aria-labelledby="evidence"><h2 id="evidence">Evidence</h2>']
    if not attested:
        parts.append("<p>All supplied evidence below is unverified because the completed launcher receipt did not verify.</p>")
    evidence = []
    for row in rows:
        for item in row.get("evidence", []):
            evidence.append("<li><p>Check " + _text(row.get("id", "UNKNOWN")) + ": " +
                            _source(item.get("source", "UNKNOWN")) + "</p>" +
                            _pairs([("Observed", item.get("observed_at", "UNKNOWN")),
                                    ("Expires", item.get("expires_at", "UNKNOWN")),
                                    ("SHA256", item.get("sha256", "UNKNOWN")),
                                    ("Runtime", item.get("runtime_sha") or "UNKNOWN"),
                                    ("Image", item.get("image_id") or "UNKNOWN")]) +
                            ("<details><summary>Recorded inventory</summary><pre>" +
                             _text(json.dumps(item.get("facts", {}), sort_keys=True, indent=2)) + "</pre></details>"
                             if row.get("id") == "02" else "") + "</li>")
    parts.append("<ul>" + "".join(evidence) + "</ul>" if evidence else "<p>No evidence recorded.</p>")
    parts += ['</section><section aria-labelledby="references"><h2 id="references">Unverified local references</h2>',
              '<p>These files do not contribute points. Their hashes identify bytes, not verified claims. '
              'Historical Markdown references remain readable; this run does not rewrite them.</p>']
    references = ["<li>" + _source(item.get("path", "UNKNOWN")) + " — SHA256 <code>" +
                  _text(item.get("sha256", "UNKNOWN")) + "</code></li>" for item in report.get("reference_files", [])]
    parts.append("<ul>" + "".join(references) + "</ul>" if references else "<p>No local references supplied.</p>")
    parts += ['</section><section aria-labelledby="limits"><h2 id="limits">Limits</h2><ul>',
              *("<li>" + _text(item) + "</li>" for item in report.get("limitations", [])),
              '</ul></section></main><footer><p>The saved JSON and its current owner-local receipt are authoritative. '
              'This HTML snapshot cannot authorize an operation or establish current readiness.</p></footer></body></html>\n']
    return "\n".join(parts)
