from __future__ import annotations

import json
import os
from typing import Any

from modules.notifications.application.service import NotificationService


class AlertRouter:
    """Routes alerts defined in alerts.json to notification service destinations.
    
    Handles severity mapping, receiver/channel routing, and triggers escalation
    if appropriate via NotificationService.
    """

    def __init__(
        self,
        notification_service: NotificationService,
        alerts_cfg_path: str | None = None,
    ) -> None:
        self.notification_service = notification_service
        if alerts_cfg_path is None:
            # Locate relative to this source file (shared/observability/alerts.py -> 3 parents up)
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            alerts_cfg_path = os.path.join(base_dir, "infra/monitoring", "alerts.json")
        self.alerts_cfg_path = alerts_cfg_path
        self._load_config()

    def _load_config(self) -> None:
        if os.path.exists(self.alerts_cfg_path):
            with open(self.alerts_cfg_path, encoding="utf-8") as f:
                self.config = json.load(f)
        else:
            self.config = {"alerts": [], "routing": {}}

    def get_alert_definition(self, alert_id: str) -> dict[str, Any] | None:
        for alert in self.config.get("alerts", []):
            if alert["id"] == alert_id:
                return alert
        return None

    def route_alert(self, alert_id: str) -> dict[str, Any]:
        alert = self.get_alert_definition(alert_id)
        if not alert:
            raise ValueError(f"Alert ID {alert_id} not found in configuration.")

        severity = alert.get("severity", "P3")
        routing = self.config.get("routing", {})
        default_receiver = routing.get("default_receiver", "admin")

        receiver = default_receiver
        routes = routing.get("routes", [])
        for route in routes:
            if route.get("severity") == severity:
                receiver = route.get("receiver", default_receiver)
                break

        return {
            "alert_id": alert_id,
            "name": alert.get("name"),
            "severity": severity,
            "metric": alert.get("metric"),
            "condition": alert.get("condition"),
            "runbook": alert.get("runbook"),
            "receiver": receiver,
        }

    def trigger_alert(self, alert_id: str, context_details: str) -> str | None:
        routed = self.route_alert(alert_id)

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
            f"Condition: {routed['condition']}\n"
            f"Runbook: {routed['runbook']}\n"
            f"Details: {context_details}"
        )

        return self.notification_service.send_notification(
            user_id=routed["receiver"],
            title=title,
            detail=detail,
            severity=mapped_severity,
            dedup_key=f"{alert_id}:{context_details[:30]}",
        )
