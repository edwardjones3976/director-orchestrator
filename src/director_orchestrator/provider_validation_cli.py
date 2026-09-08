# [[OpenAI Codex Director Coordination | GPT-6 | 2026-09-08 | 03:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:20 UTC | BEGIN]]
"""Explicit operator bootstrap or one bounded run per configured validation lane."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import urllib.request
import urllib.parse

from .provider_validation import PostgresValidationStore, ValidationRequest


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("bootstrap","validate"))
    args=parser.parse_args()
    manifest=json.loads(Path(os.environ["DIRECTOR_PROVIDER_VALIDATION_MANIFEST"]).read_text(encoding="utf-8"))
    closed={"generation","candidate","tree","expires_at","budget_units","lanes"}
    if set(manifest)-{"campaign_id"}!=closed:
        raise ValueError("closed validation generation manifest required")
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
# [[OpenAI Codex Director Coordination | GPT-6 | 2026-09-08 | 03:05 UTC | END]]
