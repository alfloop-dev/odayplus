from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from modules.adlift.domain.incrementality import IncrementalityReport


@dataclass
class InMemoryAdLiftRepository:
    _reports: dict[str, list[IncrementalityReport]] = field(default_factory=dict)

    def save_report(self, report: IncrementalityReport) -> IncrementalityReport:
        versions = self._reports.setdefault(report.campaign_id, [])
        versioned = report.with_version(
            report_version=len(versions) + 1,
            report_id=f"adlift-report-{uuid4()}",
        )
        versions.append(versioned)
        return versioned

    def latest_reports(self) -> list[IncrementalityReport]:
        return [versions[-1] for versions in self._reports.values() if versions]

    def latest_for_campaign(self, campaign_id: str) -> IncrementalityReport | None:
        versions = self._reports.get(campaign_id)
        return versions[-1] if versions else None

    def history(self, campaign_id: str) -> list[IncrementalityReport]:
        return list(self._reports.get(campaign_id, []))


__all__ = ["InMemoryAdLiftRepository"]
