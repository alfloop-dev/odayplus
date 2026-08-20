#!/usr/bin/env python3
"""Raise an alarm when the branch workflow drifts from what config promises.

`branch_workflow.drift_alarms` has described these thresholds since the
orchestrator's first commit, but nothing ever read them. The consequences were
invisible rather than absent: task PR #588 sat open for over a day and #589 for
two days against a 120-minute budget, and dev/main divergence went unnoticed
until someone compared the branches by eye. The mechanism meant to catch that
was itself dead.

Two of the three thresholds are measurable today and are implemented here.
`publish_must_promote_within_minutes` is not: the publish/release pipeline was
never built, so there is no publish branch whose promotion could be timed.
Emitting a green result for it would be worse than leaving it unwired, because
a threshold that cannot fail reads as one that is being met.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from common import parse_utc_timestamp, run_command, utc_now, write_activity_log

ALARM_TASK_PR_STALE = "task_pr_stale"
ALARM_DEV_MAIN_DIVERGED = "dev_main_diverged"

# One entry per alarm currently raised, keyed by a signature that changes when
# the underlying subject changes. Re-emitting on every ~30s supervisor cycle
# would bury the log; emitting once and never again would hide a drift that
# recurs after being cleared.
_STATE_KEY = "branch_drift_alarms"


def _minutes_between(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 60.0


def drift_alarm_settings(config: dict[str, Any]) -> dict[str, Any]:
    workflow = config.get("branch_workflow")
    if not isinstance(workflow, dict) or not workflow.get("enabled", True):
        return {}
    alarms = workflow.get("drift_alarms")
    return alarms if isinstance(alarms, dict) else {}


def branch_names(config: dict[str, Any]) -> tuple[str, str]:
    workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    dev = str(workflow.get("dev_branch") or "dev").strip() or "dev"
    main = str(workflow.get("main_branch") or "main").strip() or "main"
    return dev, main


def evaluate_drift(
    *,
    now: datetime,
    open_task_prs: list[dict[str, Any]],
    divergence_since: datetime | None,
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pure evaluation: given collected facts, which thresholds are breached?"""
    alarms: list[dict[str, Any]] = []

    pr_budget = settings.get("task_pr_must_merge_within_minutes")
    if pr_budget:
        for pr in open_task_prs:
            opened_at = parse_utc_timestamp(pr.get("createdAt"))
            if opened_at is None:
                continue
            age = _minutes_between(opened_at, now)
            if age <= float(pr_budget):
                continue
            alarms.append(
                {
                    "alarm": ALARM_TASK_PR_STALE,
                    "signature": f"{ALARM_TASK_PR_STALE}:{pr.get('number')}",
                    "pr": pr.get("number"),
                    "title": pr.get("title"),
                    "base": pr.get("baseRefName"),
                    "age_minutes": round(age, 1),
                    "threshold_minutes": float(pr_budget),
                    "message": (
                        f"Task PR #{pr.get('number')} has been open {round(age / 60, 1)}h against a "
                        f"{pr_budget}-minute budget (base `{pr.get('baseRefName')}`)."
                    ),
                }
            )

    divergence_budget = settings.get("dev_must_not_diverge_from_main_more_than_minutes")
    if divergence_budget and divergence_since is not None:
        age = _minutes_between(divergence_since, now)
        if age > float(divergence_budget):
            alarms.append(
                {
                    "alarm": ALARM_DEV_MAIN_DIVERGED,
                    # Signature carries the divergence point so a newly diverged
                    # branch re-alarms instead of being silenced by the old entry.
                    "signature": f"{ALARM_DEV_MAIN_DIVERGED}:{divergence_since.isoformat()}",
                    "diverged_since": divergence_since.isoformat(),
                    "age_minutes": round(age, 1),
                    "threshold_minutes": float(divergence_budget),
                    "message": (
                        f"dev has been diverged from main for {round(age / 60, 1)}h against a "
                        f"{divergence_budget}-minute budget."
                    ),
                }
            )

    return alarms


def collect_open_task_prs(config: dict[str, Any], repo_slug: str | None) -> list[dict[str, Any]]:
    if not repo_slug:
        return []
    workflow = config.get("branch_workflow") if isinstance(config.get("branch_workflow"), dict) else {}
    prefix = str(workflow.get("task_branch_prefix") or "task/")
    proc = run_command(
        [
            "gh", "pr", "list", "--repo", repo_slug, "--state", "open", "--limit", "100",
            "--json", "number,title,createdAt,baseRefName,headRefName,isDraft",
        ],
        timeout=30,
    )
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [pr for pr in payload if str(pr.get("headRefName") or "").startswith(prefix)]


def measure_divergence_since(config: dict[str, Any], remote: str = "origin") -> datetime | None:
    """When did dev first carry a commit that main does not have?

    None means the branches are in sync -- or that the refs are unavailable, in
    which case staying quiet is right: an alarm nobody can act on is noise.
    """
    dev, main = branch_names(config)
    proc = run_command(
        ["git", "log", f"{remote}/{main}..{remote}/{dev}", "--format=%cI"],
        timeout=30,
    )
    if proc.returncode != 0:
        return None
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        return None
    return parse_utc_timestamp(lines[-1])  # oldest commit not in main


def resolve_repo_slug(config: dict[str, Any]) -> str | None:
    # A config with no bus state path is a legitimate deployment, not an error:
    # it simply has no GitHub side to read PR ages from. Divergence is measured
    # from git alone and still works.
    try:
        from github_bus import infer_repo_slug, load_bus_state

        return infer_repo_slug(config, load_bus_state(config))
    except Exception:
        return None


def check_branch_drift(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    repo_slug: str | None = None,
    now: datetime | None = None,
) -> bool:
    settings = drift_alarm_settings(config)
    if not settings:
        return False

    moment = now or datetime.now(UTC)
    alarms = evaluate_drift(
        now=moment,
        open_task_prs=collect_open_task_prs(config, repo_slug or resolve_repo_slug(config)),
        divergence_since=measure_divergence_since(config),
        settings=settings,
    )

    supervisor_state = state.setdefault("supervisor", {})
    previous = supervisor_state.get(_STATE_KEY)
    previous_signatures = set(previous) if isinstance(previous, (list, set)) else set()
    current_signatures = {alarm["signature"] for alarm in alarms}

    changed = False
    for alarm in alarms:
        if alarm["signature"] in previous_signatures:
            continue
        write_activity_log(
            config,
            {
                "type": "branch_drift_alarm",
                "alarm": alarm["alarm"],
                "message": alarm["message"],
                "detail": {k: v for k, v in alarm.items() if k not in {"message", "signature"}},
            },
        )
        changed = True

    for signature in sorted(previous_signatures - current_signatures):
        write_activity_log(
            config,
            {
                "type": "branch_drift_alarm_cleared",
                "alarm": signature.split(":", 1)[0],
                "message": f"Branch drift alarm cleared: {signature}",
            },
        )
        changed = True

    if current_signatures != previous_signatures:
        supervisor_state[_STATE_KEY] = sorted(current_signatures)
        supervisor_state["branch_drift_checked_at"] = utc_now()
        changed = True

    return changed
