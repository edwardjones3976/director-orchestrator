"""Administrator contact wrapper for the OpenAI/Codex Director lane."""
from __future__ import annotations

from . import openai
from .common import serve


def status() -> dict:
    return {**openai.status(), "administrator": "ChatGPT/Codex",
            "adapter_scope": "advisory_only", "authority_granted": False}


def consult(text: str) -> dict:
    return {**openai.ask(text), "administrator": "ChatGPT/Codex",
            "adapter_scope": "advisory_only", "authority_granted": False}


if __name__ == "__main__":
    serve(name="admin_openai", tool_name="admin_openai_consult",
          description="Consult the OpenAI administrator contact once; no assignment or approval authority.",
          status=status, invoke=consult)
