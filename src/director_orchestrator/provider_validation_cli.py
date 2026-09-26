# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:20 UTC | BEGIN]]
"""Explicit operator bootstrap or one bounded run per configured validation lane.

The ``preflight`` action is read-only: it projects the manifest lanes through the
static inventory and one synthetic fixture, needs no DSN, owner token or bridge
URL, and makes zero provider, work or spend calls.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import urllib.request
import urllib.parse

from .provider_validation import PostgresValidationStore, ValidationRequest, read_only_lane_preflight


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("bootstrap","validate","preflight"))
    args=parser.parse_args(argv)
    manifest=json.loads(Path(os.environ["DIRECTOR_PROVIDER_VALIDATION_MANIFEST"]).read_text(encoding="utf-8"))
    if set(manifest)!={"generation","candidate","tree","expires_at","budget_units","lanes"}:
        raise ValueError("closed validation generation manifest required")
    if args.action=="preflight":
        report=read_only_lane_preflight(manifest["lanes"])
        report.update(generation=manifest["generation"],candidate=manifest["candidate"],tree=manifest["tree"])
        print(json.dumps(report,sort_keys=True))
        return 0
    owner=os.environ["DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN"]
    if args.action=="bootstrap":
        store=PostgresValidationStore(os.environ["DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN"])
        store.bootstrap()
        store.create_generation(owner_token=owner,**manifest)
        print(json.dumps({"created":True,"generation":manifest["generation"]}))
        return 0
    url=os.environ["DIRECTOR_PROVIDER_VALIDATION_BRIDGE_URL"]
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.path!="/v1/provider/validate" or parsed.query or parsed.fragment:
        raise ValueError("pinned HTTPS validation endpoint required")
    from .host_provider_bridge import _NoRedirectHandler
    opener=urllib.request.build_opener(_NoRedirectHandler)
    results={}
    for provider in manifest["lanes"]:
        request=ValidationRequest(generation=manifest["generation"],provider=provider)
        response=opener.open(urllib.request.Request(url,data=request.model_dump_json().encode(),method="POST",
            headers={"Authorization":"Bearer "+owner,"Content-Type":"application/json"}),timeout=910)
        with response:
            raw=response.read(4097)
        if len(raw)>4096:
            raise ValueError("validation response exceeds bound")
        result=json.loads(raw)
        if result.get("ok") is not True:
            raise ValueError("validation incomplete; reconcile without replay")
        results[provider]=result
    print(json.dumps({"generation":manifest["generation"],"captures":results},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:20 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-21 | 20:30 UTC | END]]
