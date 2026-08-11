from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from modules.notifications.application.service import NotificationService

_FULL_SHA_REGEX = re.compile(r"^[0-9a-fA-F]{40}$")

# Counts pages that were routed but never delivered. See try_trigger_alert.
ALERT_DELIVERY_FAILURE_METRIC = "alert_delivery_failure_count"

_logger = logging.getLogger(__name__)


def is_valid_full_sha(sha: str | None) -> bool:
    """Returns True iff sha is a valid 40-character hexadecimal git commit SHA."""
    if not sha or not isinstance(sha, str):
        return False
    return bool(_FULL_SHA_REGEX.match(sha.strip()))


class AlertRouter:
    """Routes alerts defined in alerts.json to notification service destinations.

    Handles severity mapping, receiver/channel routing, release identity contract binding,
    and triggers escalation if appropriate via NotificationService.
    """

    def __init__(
        self,
        notification_service: NotificationService,
        alerts_cfg_path: str | None = None,
        release_sha: str | None = None,
    ) -> None:
        self.notification_service = notification_service
        if alerts_cfg_path is None:
            # Locate relative to this source file (shared/observability/alerts.py -> 3 parents up)
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            alerts_cfg_path = os.path.join(base_dir, "infra/monitoring", "alerts.json")
        self.alerts_cfg_path = alerts_cfg_path
        self.release_sha = (
            release_sha
            or os.getenv("RELEASE_SHA")
            or os.getenv("TRUSTED_DEPLOYED_RELEASE_SHA")
        )
        if self.release_sha:
            self.release_sha = self.release_sha.strip()
        self._load_config()

    def _load_config(self) -> None:
        if not self.alerts_cfg_path or not os.path.exists(self.alerts_cfg_path):
            self.config = {"alerts": [], "routing": {}}
            raise ValueError(f"Alert configuration file missing or not found at '{self.alerts_cfg_path}'. Fail-closed gate enforced.")
        with open(self.alerts_cfg_path, encoding="utf-8") as f:
            self.config = json.load(f)
        self.validate_routing_config()

    def get_trusted_release_sha(self) -> str | None:
        """Resolves the trusted deployed or configured release SHA from instance attribute, environment, or config."""
        sha = (
            self.release_sha
            or os.getenv("RELEASE_SHA")
            or os.getenv("TRUSTED_DEPLOYED_RELEASE_SHA")
        )
        if not sha and hasattr(self, "config") and isinstance(self.config, dict):
            rel_id = self.config.get("release_identity", {})
            if isinstance(rel_id, dict):
                exact = rel_id.get("exact_sha_binding")
                if exact and isinstance(exact, str):
                    exact_clean = exact.strip()
                    if exact_clean == "${RELEASE_SHA}":
                        expanded = os.path.expandvars(exact_clean)
                        if expanded and not expanded.startswith("$"):
                            sha = expanded
                    elif "$" not in exact_clean and is_valid_full_sha(exact_clean):
                        sha = exact_clean
        if sha and isinstance(sha, str):
            sha = sha.strip()
            if is_valid_full_sha(sha):
                return sha
        return None

    def validate_routing_config(self) -> None:
        """Validates that alert routing configuration and release identity contract are present and fail-closed.

        If routing or default_receiver is missing/empty, or an alert severity is unmapped,
        or release_identity is missing/unbound/invalid, a ValueError is raised to fail closed.
        """
        if not self.alerts_cfg_path or not os.path.exists(self.alerts_cfg_path):
            raise ValueError(f"Alert configuration file missing or not found at '{self.alerts_cfg_path}'. Fail-closed gate enforced.")

        if not hasattr(self, "config") or not isinstance(self.config, dict):
            raise ValueError("Alert configuration is invalid. Fail-closed gate enforced.")

        release_identity = self.config.get("release_identity")
        if not release_identity or not isinstance(release_identity, dict) or not release_identity.get("bound"):
            raise ValueError("Alert release identity configuration is missing or unbound. Fail-closed gate enforced.")

        if not release_identity.get("enabled"):
            raise ValueError("Alert release identity configuration is not enabled. Fail-closed gate enforced.")

        if not release_identity.get("label_key"):
            raise ValueError("Alert release identity configuration missing label_key. Fail-closed gate enforced.")

        if "exact_sha_binding" not in release_identity:
            raise ValueError("Alert release identity configuration missing exact_sha_binding. Fail-closed gate enforced.")

        exact_binding = release_identity.get("exact_sha_binding")
        if not isinstance(exact_binding, str):
            raise ValueError("Alert release identity exact_sha_binding must be a string. Fail-closed gate enforced.")

        exact_clean = exact_binding.strip()
        if "$" in exact_clean or exact_clean.startswith("$"):
            if exact_clean != "${RELEASE_SHA}":
                raise ValueError(
                    f"Configured exact_sha_binding '{exact_binding}' is not a valid 40-character hex SHA or placeholder. Fail-closed gate enforced."
                )
            expanded_binding = os.path.expandvars(exact_clean).strip()
            if expanded_binding != "${RELEASE_SHA}":
                if not is_valid_full_sha(expanded_binding):
                    raise ValueError(
                        f"Configured exact_sha_binding '{exact_binding}' is not a valid 40-character hex SHA or placeholder. Fail-closed gate enforced."
                    )
                trusted = self.get_trusted_release_sha()
                if trusted and trusted.lower() != expanded_binding.lower():
                    raise ValueError(
                        f"Configured exact_sha_binding '{expanded_binding}' does not match trusted deployed release SHA '{trusted}'. Fail-closed gate enforced."
                    )
        else:
            if not is_valid_full_sha(exact_clean):
                raise ValueError(
                    f"Configured exact_sha_binding '{exact_binding}' is not a valid 40-character hex SHA or placeholder. Fail-closed gate enforced."
                )
            trusted = self.get_trusted_release_sha()
            if trusted and trusted.lower() != exact_clean.lower():
                raise ValueError(
                    f"Configured exact_sha_binding '{exact_clean}' does not match trusted deployed release SHA '{trusted}'. Fail-closed gate enforced."
                )

        routing = self.config.get("routing")
        if not routing or not isinstance(routing, dict):
            raise ValueError("Alert routing configuration is missing or invalid. Fail-closed gate enforced.")

        default_receiver = routing.get("default_receiver")
        routes = routing.get("routes", [])

        if not default_receiver and not routes:
            raise ValueError("Alert routing default_receiver or routes must be configured. Fail-closed gate enforced.")

        sev_map = {
            r.get("severity"): r.get("receiver")
            for r in routes
            if isinstance(r, dict) and r.get("severity") and r.get("receiver")
        }

        alerts = self.config.get("alerts", [])
        for alert in alerts:
            alert_id = alert.get("id", "unknown")
            severity = alert.get("severity", "P3")
            receiver = sev_map.get(severity) or default_receiver
            if not receiver:
                raise ValueError(
                    f"On-call route for alert '{alert_id}' (severity '{severity}') is unconfigured. Fail-closed gate enforced."
                )

            if release_identity.get("bound") and not alert.get("release_identity_bound"):
                raise ValueError(
                    f"Alert '{alert_id}' release_identity_bound is missing or false. Fail-closed gate enforced."
                )

    def resolve_release_sha(self, release_sha: str | None = None) -> str:
        """Resolves and strictly validates the release SHA identity.

        Ensures full 40-char hex SHA formatting, prevents untrusted caller overrides,
        and enforces equality with the trusted deployed/configured identity.
        """
        trusted_sha = self.get_trusted_release_sha()

        if release_sha is not None:
            candidate_sha = release_sha.strip() if isinstance(release_sha, str) else ""
            if not is_valid_full_sha(candidate_sha):
                raise ValueError(
                    f"Release identity contract violation: release_sha '{release_sha}' is not a valid 40-character hex SHA. Fail-closed gate enforced."
                )
            if not trusted_sha:
                raise ValueError(
                    "Release identity contract violation: router has no trusted deployed release SHA bound. Caller cannot supply untrusted release identity override. Fail-closed gate enforced."
                )
            if candidate_sha.lower() != trusted_sha.lower():
                raise ValueError(
                    f"Release identity contract violation: caller-supplied release SHA '{candidate_sha}' does not match trusted deployed release SHA '{trusted_sha}'. Fail-closed gate enforced."
                )
            return candidate_sha

        if not trusted_sha or not is_valid_full_sha(trusted_sha):
            raise ValueError(
                "Release identity contract violation: release_sha is unbound or invalid. Fail-closed gate enforced."
            )

        return trusted_sha

    def get_alert_definition(self, alert_id: str) -> dict[str, Any] | None:
        for alert in self.config.get("alerts", []):
            if alert.get("id") == alert_id:
                return alert
        return None

    def route_alert(self, alert_id: str, release_sha: str | None = None) -> dict[str, Any]:
        alert = self.get_alert_definition(alert_id)
        if not alert:
            raise ValueError(f"Alert ID {alert_id} not found in configuration.")

        eff_sha = self.resolve_release_sha(release_sha)

        if alert.get("release_identity_bound") is not True:
            raise ValueError(
                f"Alert '{alert_id}' release_identity_bound is not True in config. Fail-closed gate enforced."
            )

        severity = alert.get("severity", "P3")
        routing = self.config.get("routing", {})
        default_receiver = routing.get("default_receiver")

        receiver = None
        routes = routing.get("routes", [])
        for route in routes:
            if route.get("severity") == severity:
                receiver = route.get("receiver")
                break

        if not receiver:
            receiver = default_receiver

        if not receiver:
            raise ValueError(
                f"On-call route for alert '{alert_id}' (severity '{severity}') is unconfigured. Fail-closed gate enforced."
            )

        # Re-derived, not a literal. A hard-coded ``True`` made every consumer
        # -- including the completion evidence generator -- read back its own
        # assumption, so the field could never report anything but success.
        # Deriving it from the trusted identity as it stands at emission time
        # means a rotated or cleared deployed SHA downgrades the claim.
        #
        # This downgrades rather than raises: the config-declared binding is
        # already gated above, and past that point suppressing a page because
        # its provenance annotation weakened is strictly worse than delivering
        # the page with an honest annotation.
        trusted_now = self.get_trusted_release_sha()
        release_identity_bound = bool(
            trusted_now and eff_sha.lower() == trusted_now.lower()
        )

        return {
            "alert_id": alert_id,
            "name": alert.get("name"),
            "severity": severity,
            "metric": alert.get("metric"),
            "condition": alert.get("condition"),
            "runbook": alert.get("runbook"),
            "receiver": receiver,
            "release_sha": eff_sha,
            "release_identity_bound": release_identity_bound,
        }

    def trigger_alert(
        self, alert_id: str, context_details: str, release_sha: str | None = None
    ) -> str | None:
        routed = self.route_alert(alert_id, release_sha=release_sha)

        # Map P1/P2/P3 to notifications severity
        sev_map = {
            "P1": "danger",
            "P2": "warning",
            "P3": "info",
        }
        mapped_severity = sev_map.get(routed["severity"], "info")

        title = f"ALERT: [{routed['severity']}] {routed['name']}"
        detail = (
            f"Alert ID: {routed['alert_id']}\n"
            f"Release SHA: {routed['release_sha']}\n"
            f"Condition: {routed['condition']}\n"
            f"Runbook: {routed['runbook']}\n"
            f"Details: {context_details}"
        )

        return self.notification_service.send_notification(
            user_id=routed["receiver"],
            title=title,
            detail=detail,
            severity=mapped_severity,
            dedup_key=f"{alert_id}:{routed['release_sha'][:8]}:{context_details[:30]}",
        )


def _record_alert_delivery_failure(
    alert_id: str, exc: BaseException, registry: Any | None = None
) -> None:
    """Count and log a lost page.

    Runs inside the caller's own failure handling, so it must not raise: the
    failure report must not become the failure.
    """
    try:
        # Imported here, not at module scope: shared.observability.metrics and
        # .watch_window already import each other, and this module is the first
        # one the package __init__ pulls in.
        from shared.observability.metrics import default_registry

        reg = registry if registry is not None else default_registry()
        reg.increment(
            ALERT_DELIVERY_FAILURE_METRIC,
            labels={"alert_id": str(alert_id), "error_class": type(exc).__name__},
        )
    except Exception:
        pass
    try:
        _logger.error(
            "alert_delivery_failed alert_id=%s error_class=%s detail=%s",
            alert_id,
            type(exc).__name__,
            exc,
        )
    except Exception:
        pass


def try_trigger_alert(
    notification_service: NotificationService,
    alert_id: str,
    context_details: str,
    *,
    release_sha: str | None = None,
    alerts_cfg_path: str | None = None,
    registry: Any | None = None,
) -> str | None:
    """Route an alert from a terminal error path, containing delivery failures.

    ``AlertRouter`` is deliberately fail-closed: a missing config, an unmapped
    severity, or an unbound release identity raises rather than paging with
    untrusted provenance. That contract is right for a caller whose only job is
    to page, and wrong for one that pages *while* handling a failure of its own
    -- the DLQ poison-isolation branch raises before the dead-letter event is
    written, so a misconfigured alerting stack silently changes how a poison
    job is handled. Losing the page is bad; losing the dead-letter is worse.

    Router construction is inside the guard, not just the trigger: config
    loading validates the whole routing table and raises there too.

    On failure this records ``alert_delivery_failure_count`` and returns None,
    so the page is lost loudly and countably rather than silently.
    """
    try:
        router = AlertRouter(
            notification_service=notification_service,
            alerts_cfg_path=alerts_cfg_path,
            release_sha=release_sha,
        )
        return router.trigger_alert(alert_id, context_details, release_sha=release_sha)
    except Exception as exc:
        _record_alert_delivery_failure(alert_id, exc, registry)
        return None
