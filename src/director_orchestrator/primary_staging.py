# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
"""Evidence-only service: deliberately never imports the orchestration app.

No task store, provider registry, graph, writer, recovery loop, or effect adapter
is constructed. Database access is a short read-only identity transaction.
"""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI

from .config import Settings
from .langgraph_postgres import database_binding
from .primary_activation_evidence import (
    ActivationEvidenceStore, ActivationIdentity, _acceptance_secrets_from_environment,
)


def verify_staged_identity() -> None:
    settings = Settings()
    if any((settings.allow_paid_runs, settings.langgraph_primary_enabled,
            settings.langgraph_primary_new_intake_enabled,
            settings.langgraph_primary_live_provider_dispatch_enabled)):
        raise RuntimeError("staging requires all runtime execution switches disabled")
    identity, instance = database_binding(settings.langgraph_primary_postgres_dsn,
        schema=settings.langgraph_primary_postgres_schema,
        allow_insecure_local_transport=settings.langgraph_primary_allow_insecure_local_postgres)
    expected = ActivationIdentity(
        candidate_commit=settings.langgraph_primary_pinned_commit_sha,
        candidate_tree=settings.langgraph_primary_pinned_tree_sha,
        profile_id=settings.app_env,
        compose_project=os.environ.get("DIRECTOR_COMPOSE_PROJECT_NAME", ""),
        database_identity=identity, database_instance_id=instance,
        postgres_schema=settings.langgraph_primary_postgres_schema,
        checkpoint_namespace=settings.langgraph_primary_checkpoint_namespace,
    )
    from .primary_generation import require_generation
    require_generation(settings, expected)
    ActivationEvidenceStore("/data/langgraph-primary-activation-evidence.json",
        acceptance_secrets=_acceptance_secrets_from_environment(), read_only=True,
    ).require_staged(expected)


def create_app(verifier=verify_staged_identity) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        try:
            verifier()
        except Exception:
            raise RuntimeError("staging acceptance, restore or database identity verification failed") from None
        app.state.staged_ready = True
        yield
        app.state.staged_ready = False

    app = FastAPI(title="Director staged evidence", lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None)
    app.state.staged_ready = False

    @app.get("/health")
    @app.get("/v1/staging/status")
    def status():
        return {"langgraph_primary": {
            "staged_ready": app.state.staged_ready,
            "evidence_only": True,
            "primary_runtime_active": False,
            "primary_graph_constructed": False,
            "primary_router_wired": False,
            "intake_adoption_enabled": False,
            "task_mutations_enabled": False,
            "live_provider_dispatch": False,
            "effects_enabled": False,
            "startup_recovery_enabled": False,
            "reconciliation_worker_ready": False,
            "writer_heartbeat_enabled": False,
            # Staging does not claim that another service stopped or ceded ownership.
            "legacy_new_intake_disabled": False,
        }}

    return app


app = create_app()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
