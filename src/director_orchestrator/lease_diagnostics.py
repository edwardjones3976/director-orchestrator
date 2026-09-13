# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 16:58 UTC | BEGIN]]
"""Process-local observations, never durable lease authority."""
from datetime import datetime, timezone
import logging
import time

_LOG = logging.getLogger(__name__)

class LeaseRenewalDiagnostics:
    def __init__(self):
        self.last_success_at = None
        self._task_failure = None
        self._intake_failure = None
        self._last_warning = None

    def failed(self, category):
        # Closed vocabulary; no exception messages, class names, or payloads.
        if category not in {"task_writer_renewal_failed", "intake_renewal_failed"}:
            category = "renewal_failed"
        if category == "task_writer_renewal_failed":
            self._task_failure = category
        else:
            self._intake_failure = category
        now = time.monotonic()
        if self._last_warning is None or now - self._last_warning >= 60:
            _LOG.warning("Primary renewal failed: %s; observed lease authority remains required", category)
            self._last_warning = now

    @property
    def failure_category(self):
        return self._task_failure or self._intake_failure

    def task_completed(self, successful):
        if successful:
            self._task_failure = None
        else:
            self.failed("task_writer_renewal_failed")

    def succeeded(self):
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        self._intake_failure = None

    def health(self):
        return {
            "intake_lease_last_renewal_at": self.last_success_at,
            "lease_renewal_failure_category": self.failure_category,
        }
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 16:58 UTC | END]]
