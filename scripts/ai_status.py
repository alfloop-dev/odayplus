#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised only in lean supervisor envs
    yaml = None

YAML_ERROR_TYPES = (yaml.YAMLError,) if yaml is not None else ()

ROOT = Path(__file__).resolve().parents[1]


def resolve_status_root(
    env: dict[str, str] | os._Environ[str] | None = None,
    *,
    default_root: Path = ROOT,
) -> Path:
    """Resolve the canonical coordination root for status mutations.

    ``ORCH_STATUS_ROOT`` is written by the Supervisor from the dispatch
    receipt and deliberately wins over the legacy worker-facing
    ``PANTHEON_STATUS_ROOT``.  This prevents an otherwise valid worker from
    making GitHub's review row green while materializing the matching task
    transition only in a worktree-local shadow.
    """

    source = os.environ if env is None else env
    raw = str(source.get("ORCH_STATUS_ROOT") or source.get("PANTHEON_STATUS_ROOT") or "").strip()
    if not raw:
        return default_root
    return Path(os.path.expanduser(raw)).resolve()


STATUS_ROOT = resolve_status_root()
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from multi_repo_registry import (
    repository_local_path,
    repository_slug,
    resolve_repository,
    task_artifact_repository_ids,
    task_primary_repository_id,
)
from runtime_state import load_runtime_state
from task_archive import (
    ARCHIVE_TASKS_DIR,
    TaskResolver,
    archive_display_path,
    archive_task_path,
    archive_task_snapshot,
    is_terminal_task,
    load_archive_index,
    load_archived_snapshot,
    rebuild_archive_index,
    recent_terminal_summaries,
)
from task_archive import (
    DEFAULT_RECENT_LIMIT as DEFAULT_ARCHIVE_RECENT_LIMIT,
)

STATUS_FILE = STATUS_ROOT / "ai-status.json"
LOG_FILE = STATUS_ROOT / "ai-activity-log.jsonl"
LOG_ROTATE_MAX_BYTES = int(os.environ.get("AI_STATUS_LOG_ROTATE_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_ROTATE_KEEP_LINES = int(os.environ.get("AI_STATUS_LOG_ROTATE_KEEP_LINES", "1000"))
CURRENT_WORK_FILE = STATUS_ROOT / "current-work.md"
DOCS_SITE_DIR = STATUS_ROOT / "docs-site"
CONFIG_FILE = ROOT / ".orchestrator" / "config.json"
# The live Supervisor reads its fleet through common.load_config(), which deep-
# merges `.orchestrator/config.json` with the gitignored `.orchestrator/
# config.local.json` overlay. Worker worktrees only get the tracked half, so the
# status-root copy is consulted as well; in the live checkout both paths are the
# same file and the merge is a no-op.
CONFIG_LOCAL_FILE = ROOT / ".orchestrator" / "config.local.json"
STATUS_ROOT_CONFIG_LOCAL_FILE = STATUS_ROOT / ".orchestrator" / "config.local.json"
PLANNING_STATE_FILE = STATUS_ROOT / ".orchestrator" / "planning-state.json"
ORCHESTRATOR_STATE_FILE = STATUS_ROOT / ".orchestrator" / "state.json"
APPROVAL_QUEUE_FILE = STATUS_ROOT / ".orchestrator" / "approval-queue.json"
DASHBOARD_BUNDLE_FILE = STATUS_ROOT / "dashboard-bundle.json"
DEFAULT_PLANNING_README = "docs/02-architecture/consensus/phase1/README.md"
DEFAULT_PLANNING_SESSION_FILE = "docs/02-architecture/consensus/phase1/planning-session.json"
DEFAULT_PLANNING_CHECKLIST_FILE = "docs/02-architecture/consensus/phase1/pantheon-backend-completion-checklist.md"

KNOWN_AGENTS = {
    "Claude": {
        "capability_lane": ["execution", "control-plane", "governance-review"],
        "default_branch": "feat/claude-execution-control",
        "target_workload": 20,
    },
    "Claude2": {
        "capability_lane": ["execution", "control-plane", "governance-review"],
        "default_branch": "feat/claude2-execution-control",
        "target_workload": 10,
    },
    "Antigravity": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity-research-runtime",
        "target_workload": 70,
    },
    "Antigravity2": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity2-research-runtime",
        "target_workload": 5,
    },
    "Antigravity3": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity3-research-runtime",
        "target_workload": 5,
    },
    "Antigravity4": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity4-research-runtime",
        "target_workload": 5,
    },
    "Antigravity5": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity5-research-runtime",
        "target_workload": 5,
    },
    "Antigravity6": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity6-research-runtime",
        "target_workload": 5,
    },
    "Antigravity7": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/antigravity7-research-runtime",
        "target_workload": 5,
    },
    "Gemini": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/gemini-research-runtime",
        "target_workload": 5,
    },
    "Gemini2": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/gemini2-research-runtime",
        "target_workload": 5,
    },
    "Codex": {
        "capability_lane": ["integration", "status-system", "schema", "acceptance"],
        "default_branch": "feat/codex-collab-system",
        "target_workload": 5,
    },
    "Codex2": {
        "capability_lane": ["integration", "status-system", "schema", "acceptance"],
        "default_branch": "feat/codex-collab-system",
        "target_workload": 5,
    },
    "Copilot": {
        "capability_lane": ["research-ingest", "external-search", "spec-review", "critique"],
        "default_branch": "feat/copilot-research-critique",
        "target_workload": 5,
    },
    "Human/Ops": {
        "capability_lane": ["human-gate", "operations", "signoff"],
        "default_branch": "human/ops",
        "target_workload": 0,
    },
}

# KNOWN_AGENTS is mutated at runtime by ensure_agent(). This frozen snapshot is
# NOT an authority for actor validation — a name that only appears here is
# rejected as an actor reference. It exists solely so the cleanup path can tell
# that recompute_agents() will recreate a static lane's roster row, and
# therefore not advertise it as removable.
BASELINE_KNOWN_AGENT_NAMES = frozenset(KNOWN_AGENTS)

# Actors that legitimately appear in actor-shaped fields without ever being a
# dispatchable worker: the human gate, the supervisor's own audit identity, and
# the Codex coordination lane. They are declared here because the Supervisor
# config only enumerates dispatch targets.
NON_WORKER_ACTORS = frozenset({"Human/Ops", "Orchestrator", "CodexCoordinator"})

AGENT_ALIASES = {
    "claude2": "Claude2",
    "claude 2": "Claude2",
    "gemini2": "Gemini2",
    "gemini 2": "Gemini2",
    "antigravity": "Antigravity",
    "antigravity2": "Antigravity2",
    "antigravity3": "Antigravity3",
    "antigravity4": "Antigravity4",
    "antigravity5": "Antigravity5",
    "antigravity6": "Antigravity6",
    "antigravity7": "Antigravity7",
    "agy": "Antigravity",
    "agy2": "Antigravity2",
    "agy3": "Antigravity3",
    "agy4": "Antigravity4",
    "agy5": "Antigravity5",
    "agy6": "Antigravity6",
    "agy7": "Antigravity7",
    "codex2": "Codex2",
    "codex (2)": "Codex2",
    # No "codex3" alias: config.local.json declares Codex3 as its own worker,
    # so folding it into Codex would silently reassign a real fleet member.
    "grok": "Copilot",
    "copilot": "Copilot",
    "copilot host": "Copilot",
    "copilot_host": "Copilot",
    "human": "Human/Ops",
    "human ops": "Human/Ops",
    "human/ops": "Human/Ops",
    "human-ops": "Human/Ops",
    "ops": "Human/Ops",
}

RETIRED_AGENT_REPLACEMENTS = {}

STATUS_LABELS = {
    "todo": "todo",
    "in_progress": "in_progress",
    "review": "review",
    "review_approved": "review_approved",
    "blocked": "blocked",
    "done": "done",
}

DEPENDENCY_DONE_STATUSES = {"done"}
ACTIVE_TASK_STATUSES = {"todo", "in_progress", "review", "review_approved", "blocked"}
EXTERNAL_TASK_PREFIXES = {"OC", "RS", "LP", "OSS", "SPIKE"}
EXTERNAL_TASK_ID_TOKENS = {
    "DATASOURCE",
    "OPENCLAW",
    "OSS",
    "SEARCH",
    "SOURCE",
}
EXTERNAL_TASK_TEXT_KEYWORDS = {
    "external",
    "external source",
    "external search",
    "openclaw",
    "oss",
    "searchgateway",
    "source/search",
    "source-ingest",
    "source_ingestion",
}
EXTERNAL_TASK_ARTIFACT_PREFIXES = (
    "integrations/",
    "services/openclaw",
    "services/search",
    "services/source_ingestion",
)
TASK_TERMINAL_SUPERSEDED = "superseded"
DEFAULT_DELIVERY_GATES = {
    "require_commit_hash": True,
    "require_git_clean": True,
    "record_remote_status": True,
    "require_merged_pr": True,
}
DEFAULT_COMMIT_CONVENTIONS = {
    "subject_must_include_task_id": True,
    "required_body_fields": ["LLM-Agent", "Task-ID", "Reviewer"],
}
FIRST_PROMPT_PRIORITY = [
    "AI_COLLABORATION_GUIDE.md",
    "ai-status.json",
    "TARGET_ARCHITECTURE.md",
    "CANONICAL_DOCUMENT_MAP.md",
    "ROADMAP.md",
    "DEVELOPMENT_WORKBREAKDOWN.md",
    "WORKBENCH_DELIVERY_BACKLOG.md",
    "DELIVERY_CLOSURE_AND_LOOP_STATES.md",
    "EXECUTION_PROOF_AND_MATURITY_LEVELS.md",
]
OPTIONAL_CURRENT_WORK_REFERENCES = (
    ("CANONICAL_DOCUMENT_MAP.md", "Canonical map"),
    ("DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md", "Document boundary"),
    ("DEVELOPMENT_WORKBREAKDOWN.md", "Full backlog"),
    ("WORKBENCH_DELIVERY_BACKLOG.md", "Workbench backlog"),
    ("DELIVERY_CLOSURE_AND_LOOP_STATES.md", "Loop closure"),
    ("EXECUTION_PROOF_AND_MATURITY_LEVELS.md", "Execution proof"),
)
DISPLAY_TIMEZONE = ZoneInfo("Asia/Taipei")
DISPLAY_TIMEZONE_LABEL = "台灣時間 (UTC+8)"
ISO_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b")
FEATURE_MODULE_RE = re.compile(r"^([A-Z]+-\d{2,3})(?:-|$)")


def default_canonical_document_layers() -> dict[str, list[str]]:
    return {
        "L0 Collaboration & State": [
            "AI_COLLABORATION_GUIDE.md",
            "ai-status.json",
            "ai-activity-log.jsonl",
        ],
        "L0.5 Derived Narrative": [
            "current-work.md",
        ],
        "L1 Platform Architecture & Policy": [
            "TARGET_ARCHITECTURE.md",
            "OPENCLAW_RUNTIME_CONTRACT.md",
            "PERSONA_RUNTIME_MODEL.md",
            "BINDING_AND_DEPLOYMENT_SEMANTICS.md",
            "PAPER_CANARY_LIVE_POLICY.md",
            "ROLLBACK_AND_POSITION_SEMANTICS.md",
            "LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md",
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md",
            "CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md",
            "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md",
            "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md",
            "TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md",
            "DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md",
            "EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md",
            "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md",
            "BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md",
            "LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md",
        ],
        "L2 Planning & Execution": [
            "CANONICAL_DOCUMENT_MAP.md",
            "DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md",
            "ROADMAP.md",
            "DEVELOPMENT_WORKBREAKDOWN.md",
            "WORKBENCH_DELIVERY_BACKLOG.md",
            "DELIVERY_CLOSURE_AND_LOOP_STATES.md",
            "EXECUTION_PROOF_AND_MATURITY_LEVELS.md",
            "OSS_INTEGRATION_CHECKLIST.md",
        ],
        "L3 Supporting Design & Migration": [
            "CANONICAL_CONTRACT_MIGRATION_DECISION.md",
            "WORK_REBASELINE.md",
            "Pantheon_總索引版系統分析文件.md",
            "Pantheon_資料表_Schema_設計版.md",
            "Pantheon_API_Service_Contract_設計版.md",
        ],
    }


def flatten_canonical_document_layers(layers: dict[str, list[str]]) -> list[str]:
    flattened: list[str] = []
    for documents in layers.values():
        for document in documents:
            if document not in flattened:
                flattened.append(document)
    return flattened


def sync_canonical_document_metadata(state: dict[str, Any]) -> None:
    default_layers = default_canonical_document_layers()
    layers = state.get("canonical_document_layers")
    merge_default_layers = str(state.get("project") or "").strip() in {"", "pantheon"}
    if not isinstance(layers, dict) or not layers:
        layers = default_layers
    else:
        normalized_layers: dict[str, list[str]] = {}
        for key, value in layers.items():
            if isinstance(value, list):
                normalized_layers[str(key)] = [str(item) for item in value]
        if not normalized_layers:
            normalized_layers = default_layers
        elif merge_default_layers:
            for key, default_documents in default_layers.items():
                existing_documents = normalized_layers.get(key, [])
                merged_documents = list(existing_documents)
                for document in default_documents:
                    if document not in merged_documents:
                        merged_documents.append(document)
                normalized_layers[key] = merged_documents
        layers = normalized_layers
    current_work = "current-work.md"
    derived_layer = "L0.5 Derived Narrative"
    removed_current_work = False
    for key, documents in layers.items():
        if key == derived_layer:
            continue
        filtered = [document for document in documents if document != current_work]
        if len(filtered) != len(documents):
            removed_current_work = True
            layers[key] = filtered
    derived_documents = [str(item) for item in layers.get(derived_layer, []) if str(item).strip()]
    if current_work in derived_documents:
        derived_documents = [document for document in derived_documents if document != current_work]
    derived_payload = [current_work, *derived_documents] if (removed_current_work or derived_documents) else None
    if derived_payload is None and derived_layer in layers:
        derived_payload = [current_work]

    reordered_layers: dict[str, list[str]] = {}
    inserted = False
    for key, documents in layers.items():
        if key == derived_layer:
            continue
        reordered_layers[key] = documents
        if key == "L0 Collaboration & State" and derived_payload is not None:
            reordered_layers[derived_layer] = derived_payload
            inserted = True

    if derived_payload is not None and not inserted:
        reordered_layers[derived_layer] = derived_payload

    if not reordered_layers and derived_payload is not None:
        reordered_layers[derived_layer] = derived_payload

    layers = reordered_layers
    state["canonical_document_layers"] = layers
    state["canonical_files"] = flatten_canonical_document_layers(layers)


def canonical_file_set(state: dict[str, Any]) -> set[str]:
    sync_canonical_document_metadata(state)
    return {
        str(item)
        for item in state.get("canonical_files", [])
        if str(item).strip()
    }


def canonical_tier_labels(state: dict[str, Any]) -> list[str]:
    sync_canonical_document_metadata(state)
    layers = state.get("canonical_document_layers", {})
    return [f"`{name}`" for name in layers]


def human_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def unique_strings(items: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def planning_reference_files(planning_state: dict[str, Any] | None) -> list[str]:
    if not planning_state:
        return []
    artifacts = planning_state.get("artifacts", {}) if isinstance(planning_state.get("artifacts"), dict) else {}
    files = [
        str(((artifacts.get("planning_readme") or {}).get("path")) or DEFAULT_PLANNING_README).strip(),
        str(planning_state.get("session_file") or ((artifacts.get("planning_session") or {}).get("path")) or DEFAULT_PLANNING_SESSION_FILE).strip(),
    ]
    files.extend(str(item).strip() for item in planning_state.get("brief_files", []) if str(item).strip())
    checklist_path = str(((artifacts.get("backend_completion_checklist") or {}).get("path")) or "").strip()
    if checklist_path:
        files.append(checklist_path)
    return unique_strings(files)


def build_onboarding_prompt(state: dict[str, Any]) -> str:
    canonical_files = canonical_file_set(state)
    prompt_files = [item for item in FIRST_PROMPT_PRIORITY if item in canonical_files]
    if not prompt_files:
        prompt_files = FIRST_PROMPT_PRIORITY[:2]

    parts = [f"Read {human_join(prompt_files)} first."]
    if "current-work.md" in canonical_files:
        parts.append("Use current-work.md as a human summary only; do not treat it as the primary machine context.")
    if "ai-activity-log.jsonl" in canonical_files:
        parts.append("Use ai-activity-log.jsonl only when you need targeted recent history.")
    planning_state = load_planning_state()
    if planning_state and planning_state.get("status") in {"active", "human_required"}:
        planning_files = planning_reference_files(planning_state)[:4]
        if planning_files:
            parts.append(
                f"Discussion planning is {planning_state['status']}; read {human_join(planning_files)} before implementation work."
            )
    parts.append("Treat generated views as derived from machine-readable state.")
    parts.append("Follow the canonical lifecycle todo -> in_progress -> review -> review_approved -> done.")
    parts.append("Use scripts/ai-status.sh for every state change.")
    return " ".join(parts)


def iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def format_display_timestamp(value: Any) -> str:
    parsed = parse_timestamp(value)
    if parsed is None:
        return "-" if value is None or value == "" else str(value)
    return parsed.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def localize_embedded_timestamps(text: Any) -> str:
    if text is None:
        return "-"
    rendered = str(text)
    if not rendered:
        return "-"
    return ISO_TIMESTAMP_RE.sub(lambda match: format_display_timestamp(match.group(0)), rendered)


def canonical_agent_name(name: str | None) -> str:
    if name is None:
        return ""
    trimmed = str(name).strip()
    if not trimmed:
        return ""
    lowered = trimmed.lower()
    # Case-fold against the declared fleet as well as the in-process table, so a
    # config.local.json worker such as `codex5` resolves to its declared
    # spelling `Codex5` instead of being treated as a new name.
    canonical_by_lower = {agent.lower(): agent for agent in KNOWN_AGENTS}
    canonical_by_lower.update({name.lower(): name for name in registered_agent_names()})
    if lowered in canonical_by_lower:
        return canonical_by_lower[lowered]
    alias_target = AGENT_ALIASES.get(lowered)
    if alias_target:
        return alias_target
    return trimmed


def active_agent_name(name: str | None) -> str:
    canonical = canonical_agent_name(name)
    replacement = RETIRED_AGENT_REPLACEMENTS.get(canonical.lower())
    return replacement or canonical


# ---------------------------------------------------------------------------
# Actor reference validation
#
# Every actor-shaped field (task owner/reviewer/waiting_for, blocker
# owner/waiting_for, handoff from/to, AI_NAME) must hold an *agent name*, never
# free prose and never a task id. Before this guard existed, ensure_agent()
# happily invented a roster entry for whatever string it was handed, so a
# worker that passed a blocker sentence where an agent name was expected
# permanently fabricated a synthetic fleet agent in ai-status.json.
#
# Two different strictness levels are needed:
#   * CLI input  -> reject loudly, before any durable state is mutated.
#   * durable state already on disk -> tolerate, so a single bad record cannot
#     brick every ai_status command (the 2026-07-20 fleet-wide stall).
# ---------------------------------------------------------------------------

ACTOR_REFERENCE_MAX_LENGTH = 40
# One or two `/`-separated segments of letters/digits/`_`/`-`, each starting
# with a letter. Matches Claude2, Antigravity7, CodexCoordinator, Human/Ops.
_ACTOR_SEGMENT = r"[A-Za-z][A-Za-z0-9_-]*"
ACTOR_REFERENCE_RE = re.compile(rf"^{_ACTOR_SEGMENT}(?:/{_ACTOR_SEGMENT})?$")
# Upper-case, >=3 dash-separated segments: ODP-P10-FLEET-CONFLICT-REAUDIT-001.
TASK_ID_LIKE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+){2,}$")

# Names registered during this process because they already appear in durable
# state but are not declared anywhere. Kept out of the durable roster so a
# corrupt record cannot grow new synthetic fleet agents on every sync.
QUARANTINED_AGENTS: set[str] = set()


def actor_reference_problem(name: str | None) -> str | None:
    """Return why `name` is unusable as an actor reference, else None.

    `name` is expected to be already canonicalized (aliases resolved), so
    alias spellings such as "human ops" or "codex (3)" never reach here.
    """
    if name is None:
        return "actor reference is missing"
    raw = str(name)
    trimmed = raw.strip()
    if not trimmed:
        return "actor reference is empty"
    if len(trimmed) > ACTOR_REFERENCE_MAX_LENGTH:
        return (
            f"actor reference is {len(trimmed)} characters "
            f"(max {ACTOR_REFERENCE_MAX_LENGTH}); this looks like prose, not an agent name"
        )
    if any(char in trimmed for char in "\n\r\t"):
        return "actor reference contains control characters"
    if TASK_ID_LIKE_RE.match(trimmed):
        return "actor reference looks like a task id, not an agent name"
    if not ACTOR_REFERENCE_RE.match(trimmed):
        return (
            "actor reference must be an agent name such as 'Claude2' or 'Human/Ops' "
            "(letters, digits, '_', '-', at most one '/')"
        )
    return None


def _fallback_deep_merge(base: Any, overlay: Any) -> Any:
    """Local copy of common.deep_merge for environments without the orchestrator package."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = deepcopy(base)
        for key, value in overlay.items():
            merged[key] = _fallback_deep_merge(merged[key], value) if key in merged else deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        return deepcopy(overlay)
    return deepcopy(overlay)


def _orchestrator_common() -> Any | None:
    try:
        import common as orchestrator_common
    except Exception:  # pragma: no cover - lean environments without the package
        return None
    return orchestrator_common


def local_config_overlay_paths() -> list[Path]:
    """Local overlays to merge on top of CONFIG_FILE, in application order."""
    paths: list[Path] = [CONFIG_FILE.with_name("config.local.json")]
    if STATUS_ROOT_CONFIG_LOCAL_FILE not in paths:
        paths.append(STATUS_ROOT_CONFIG_LOCAL_FILE)
    return [path for path in paths if path.exists()]


def _read_config_json(path: Path) -> dict[str, Any]:
    """Tolerant JSON read used only by the actor-authority path.

    Deliberately independent of `load_json_file()`: actor validation runs inside
    almost every command, so it must not be perturbed by tests or callers that
    patch the general-purpose reader.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _config_fingerprint() -> tuple[Any, ...]:
    fingerprint: list[Any] = []
    for path in (CONFIG_FILE, CONFIG_FILE.with_name("config.local.json"), STATUS_ROOT_CONFIG_LOCAL_FILE):
        try:
            stat = path.stat()
        except OSError:
            fingerprint.append((str(path), None))
        else:
            fingerprint.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(fingerprint)


_MERGED_CONFIG_CACHE: dict[str, Any] = {}


def merged_orchestrator_config() -> dict[str, Any]:
    """The config as the live Supervisor sees it.

    Dispatchability is decided by `common.load_config()`, which deep-merges
    `.orchestrator/config.json` with `.orchestrator/config.local.json`. That
    function is used verbatim when this process points at the same config path,
    so the two can never drift; otherwise the same deep-merge is applied to
    whatever `CONFIG_FILE` resolves to.

    Cached against the config files' mtime/size, because actor validation is on
    the hot path of every command.
    """
    fingerprint = _config_fingerprint()
    if _MERGED_CONFIG_CACHE.get("fingerprint") == fingerprint:
        return _MERGED_CONFIG_CACHE["payload"]

    common = _orchestrator_common()
    merge = getattr(common, "deep_merge", None) or _fallback_deep_merge
    applied: set[Path] = set()

    if common is not None and CONFIG_FILE == getattr(common, "DEFAULT_CONFIG_PATH", None):
        loaded = common.load_config()
        payload: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
        local_path = getattr(common, "LOCAL_CONFIG_PATH", None)
        if local_path is not None:
            applied.add(local_path)
    else:
        payload = _read_config_json(CONFIG_FILE)

    for overlay_path in local_config_overlay_paths():
        if overlay_path in applied:
            continue
        overlay = _read_config_json(overlay_path)
        if overlay:
            payload = merge(payload, overlay)
            applied.add(overlay_path)

    _MERGED_CONFIG_CACHE["fingerprint"] = fingerprint
    _MERGED_CONFIG_CACHE["payload"] = payload
    return payload


def extra_actor_names() -> set[str]:
    """Actor names registered out-of-band through AI_STATUS_EXTRA_AGENTS."""
    return {
        name
        for name in (raw.strip() for raw in parse_csv_env("AI_STATUS_EXTRA_AGENTS"))
        if name and actor_reference_problem(name) is None
    }


def configured_agent_names() -> set[str]:
    """Worker names the live Supervisor can dispatch.

    Reads the merged config (see `merged_orchestrator_config`), so the workers
    declared only in the gitignored `config.local.json` overlay — Codex3 through
    Codex9 on this fleet — count as declared exactly as they do for dispatch.
    Names are taken as declared and are deliberately not passed through
    `canonical_agent_name`: this function is what teaches that function how the
    fleet spells itself.
    """
    names: set[str] = set()
    agents = merged_orchestrator_config().get("agents")
    if isinstance(agents, dict):
        for agent_id, agent in agents.items():
            entry = agent if isinstance(agent, dict) else {}
            declared = str(entry.get("display_name") or agent_id or "").strip()
            if declared and actor_reference_problem(declared) is None:
                names.add(declared)
    return names


def registered_agent_names() -> set[str]:
    """Actor names this fleet accepts from the CLI.

    Authority is exactly three things: the merged Supervisor config, the
    explicitly declared non-worker actors, and AI_STATUS_EXTRA_AGENTS.

    Two tempting sources are deliberately excluded. `KNOWN_AGENTS` is mutated at
    runtime by `ensure_agent()`, so admitting it would let a name that was
    invented earlier in the same process validate later calls. The durable
    `agents[]` roster is exactly where fabricated entries land, so admitting it
    would let one bad record legitimise itself on the next command. Names that
    only exist in those two places (retired lanes such as Gemini or Copilot, or
    a synthetic entry) are readable in state but are not accepted as new input.
    """
    names = configured_agent_names() | extra_actor_names() | set(NON_WORKER_ACTORS)
    return {name for name in names if actor_reference_problem(name) is None}


def resolve_actor_reference(
    raw: str | None,
    *,
    field: str,
    allow_empty: bool = False,
) -> str:
    """Validate a caller-supplied actor reference, or abort before mutating state."""
    canonical = active_agent_name(raw)
    if not canonical:
        if allow_empty:
            return ""
        raise SystemExit(f"Invalid {field}: actor reference is required")

    problem = actor_reference_problem(canonical)
    if problem is not None:
        preview = canonical if len(canonical) <= 80 else canonical[:77] + "..."
        raise SystemExit(
            f"Invalid {field}: {problem}\n"
            f"  received: {preview!r}\n"
            "  Pass an agent name here and put the explanation in the message argument."
        )

    registered = registered_agent_names()
    if canonical not in registered:
        raise SystemExit(
            f"Unknown {field}: {canonical!r} is not a registered agent.\n"
            f"  registered: {', '.join(sorted(registered))}\n"
            "  Declare it under `agents` in .orchestrator/config.json or "
            ".orchestrator/config.local.json (the same merged config the "
            "Supervisor dispatches from), or set AI_STATUS_EXTRA_AGENTS to "
            "register it explicitly. Being present in ai-status.json agents[] "
            "is not a declaration."
        )
    ensure_agent(canonical)
    return canonical


def current_actor_validated(default: str = "Codex") -> str:
    """The one way a command may learn who is calling it.

    There used to be an unvalidated sibling, `current_actor()`, that merely
    canonicalized `AI_NAME`. Commands that used it let a malformed or
    unregistered `AI_NAME` reach durable state and the activity log, which is
    how prose ended up in actor-shaped fields in the first place. It is deleted
    rather than deprecated: every mutating command must read its actor here, and
    must do so before its first mutation, so a bad `AI_NAME` fails closed.
    `test_no_unvalidated_actor_read_remains` keeps it that way.
    """
    return resolve_actor_reference(os.environ.get("AI_NAME", default), field="AI_NAME")


def default_state() -> dict[str, Any]:
    timestamp = iso_now()
    canonical_layers = default_canonical_document_layers()
    return {
        "project": "pantheon",
        "sprint": "2026-04-09-canonical-adoption-platform-plan",
        "objective": (
            "Adopt the layered canonical document system, align architecture and planning truth, and publish the "
            "full Pantheon platform backlog without overwriting historical collaboration records."
        ),
        "updated_at": timestamp,
        "canonical_document_layers": canonical_layers,
        "canonical_files": flatten_canonical_document_layers(canonical_layers),
        "agents": [
            {
                "name": name,
                "capability_lane": meta["capability_lane"],
                "status": "idle",
                "current_task_ids": [],
                "branch": meta["default_branch"],
                "next": "",
                "last_update": None,
            }
            for name, meta in KNOWN_AGENTS.items()
        ],
        "tasks": [
            {
                "id": "P1-001",
                "title": "Define SignalStoreClient contract",
                "phase": "Phase 1",
                "owner": "Codex",
                "reviewer": "Gemini",
                "status": "todo",
                "depends_on": [],
                "artifacts": ["services/signal-store/client.py"],
                "acceptance": [
                    "interface documented",
                    "example payload added",
                    "consumer assumptions listed",
                ],
                "next": "Lock interface for downstream work",
                "last_update": None,
            },
            {
                "id": "P2-001",
                "title": "Define signal JSON schema",
                "phase": "Phase 2",
                "owner": "Gemini",
                "reviewer": "Claude",
                "status": "todo",
                "depends_on": ["P1-001"],
                "artifacts": ["services/research/schema.json"],
                "acceptance": [
                    "payload keys documented",
                    "worker contract references same schema",
                ],
                "next": "Publish worker payload contract",
                "last_update": None,
            },
            {
                "id": "P3-001",
                "title": "Wire LEAN runtime signal consumer",
                "phase": "Phase 3",
                "owner": "Claude",
                "reviewer": "Gemini",
                "status": "todo",
                "depends_on": ["P1-001", "P2-001"],
                "artifacts": ["services/execution/lean-runtime/"],
                "acceptance": [
                    "runtime can consume signal payload",
                    "broker config edge documented",
                ],
                "next": "Connect signal intake to execution plane",
                "last_update": None,
            },
            {
                "id": "P4-001",
                "title": "Draft control-plane routing contract",
                "phase": "Phase 4",
                "owner": "Claude",
                "reviewer": "Codex",
                "status": "todo",
                "depends_on": ["P2-001"],
                "artifacts": ["services/control-plane/router/"],
                "acceptance": [
                    "router contract documented",
                    "monitoring handoff documented",
                ],
                "next": "Define router and monitoring handoff",
                "last_update": None,
            },
        ],
        "handoffs": [],
        "blockers": [],
        "workload": {name: meta["target_workload"] for name, meta in KNOWN_AGENTS.items()},
    }


def load_state() -> dict[str, Any]:
    if not STATUS_FILE.exists() or STATUS_FILE.read_text(encoding="utf-8").strip() == "":
        return default_state()
    state = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    return state


def load_logs() -> list[dict[str, Any]]:
    if not LOG_FILE.exists():
        return []
    logs: list[dict[str, Any]] = []
    for line_no, line in enumerate(LOG_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(
                f"Warning: skipping malformed ai-activity-log.jsonl line {line_no}: {exc}",
                file=sys.stderr,
            )
    return logs


def load_log_tail_lines(max_lines: int = 5000) -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            block_size = 1 << 16
            buffer = bytearray()
            line_count = 0
            position = file_size
            while position > 0 and line_count <= max_lines:
                read_size = min(block_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                buffer[0:0] = chunk
                line_count = buffer.count(b"\n")
            tail = bytes(buffer)
        if line_count > max_lines:
            split_at = -1
            extra = line_count - max_lines
            for _ in range(extra):
                split_at = tail.find(b"\n", split_at + 1)
                if split_at == -1:
                    break
            if split_at != -1:
                tail = tail[split_at + 1 :]
        return tail.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def load_planning_state() -> dict[str, Any] | None:
    if not PLANNING_STATE_FILE.exists():
        return None
    try:
        payload = json.loads(PLANNING_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return deepcopy(default)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return deepcopy(default)


def load_config() -> dict[str, Any]:
    config_source = CONFIG_FILE
    if not config_source.exists():
        example = ORCHESTRATOR_DIR / "config.example.json"
        if example.exists():
            config_source = example
    payload = load_json_file(config_source, {})
    if not isinstance(payload, dict):
        return {}
    paths = payload.setdefault("paths", {})
    if isinstance(paths, dict):
        paths.update(
            {
                "status_file": str(STATUS_FILE),
                "activity_log": str(LOG_FILE),
                "current_work": str(CURRENT_WORK_FILE),
                "dashboard": str(DOCS_SITE_DIR / "index.html"),
                "state_file": str(ORCHESTRATOR_STATE_FILE),
                "event_queue": str(STATUS_ROOT / ".orchestrator" / "event-queue.jsonl"),
                "approval_queue": str(APPROVAL_QUEUE_FILE),
                "github_bus_state": str(STATUS_ROOT / ".orchestrator" / "github-bus-state.json"),
                "github_webhook_events": str(STATUS_ROOT / ".orchestrator" / "github-webhook-events.jsonl"),
                "github_relay_state": str(STATUS_ROOT / ".orchestrator" / "github-relay-state.json"),
                "provider_capabilities": str(STATUS_ROOT / ".orchestrator" / "provider_capabilities.json"),
            }
        )
    return payload


def bool_config_setting(settings: dict[str, Any], key: str, default: bool = False) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def int_config_setting(settings: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def optional_int_config_setting(settings: dict[str, Any], key: str) -> int | None:
    value = settings.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def int_mapping_config_setting(settings: dict[str, Any], key: str) -> dict[str, int]:
    raw = settings.get(key)
    if not isinstance(raw, dict):
        return {}
    values: dict[str, int] = {}
    for name, value in raw.items():
        try:
            values[str(name)] = int(value)
        except (TypeError, ValueError):
            continue
    return values


def build_dispatch_policy_summary(config: dict[str, Any]) -> dict[str, Any]:
    ready_dispatcher = config.get("ready_dispatcher") if isinstance(config.get("ready_dispatcher"), dict) else {}
    helper_claim = ready_dispatcher.get("helper_claim") if isinstance(ready_dispatcher.get("helper_claim"), dict) else {}
    worker_self_claim = ready_dispatcher.get("worker_self_claim") if isinstance(ready_dispatcher.get("worker_self_claim"), dict) else {}
    claim_idle_work = bool_config_setting(helper_claim, "claim_idle_work", False)
    helper_claim_enabled = bool_config_setting(helper_claim, "enabled", True)
    worker_self_claim_enabled = bool_config_setting(worker_self_claim, "enabled", False)
    return {
        "mode": "worker_self_claim" if worker_self_claim_enabled else ("idle_worker_claim" if helper_claim_enabled and claim_idle_work else "supervisor_owned_dispatch"),
        "worker_self_claim_enabled": worker_self_claim_enabled,
        "worker_self_claim_command": worker_self_claim.get("claim_command") or "",
        "helper_claim_enabled": helper_claim_enabled,
        "claim_idle_work": claim_idle_work,
        "claim_sidecars_when_idle": bool_config_setting(helper_claim, "claim_sidecars_when_idle", False),
        "require_owner_higher_priority_load": bool_config_setting(helper_claim, "require_owner_higher_priority_load", True),
        "owned_work_first": True,
        "max_dispatches_per_tick": int_config_setting(ready_dispatcher, "max_dispatches_per_tick", 4),
        "max_tasks_per_agent": optional_int_config_setting(ready_dispatcher, "max_tasks_per_agent"),
        "max_tasks_per_agent_by_agent": int_mapping_config_setting(ready_dispatcher, "max_tasks_per_agent_by_agent"),
        "max_concurrent_per_quota_group": int_mapping_config_setting(ready_dispatcher, "max_concurrent_per_quota_group"),
        "sidecar_only_agents": ready_dispatcher.get("sidecar_only_agents") or [],
        "disabled_agents": ready_dispatcher.get("disabled_agents") or [],
    }


def _dashboard_agent_id(config: dict[str, Any], agent_name: str | None) -> str:
    raw = str(agent_name or "").strip()
    canonical = canonical_agent_name(raw)
    candidates = {
        raw.lower(),
        canonical.lower(),
        raw.lower().replace("-", "_"),
        canonical.lower().replace("-", "_"),
    }
    for agent_id, agent in (config.get("agents", {}) or {}).items():
        display_name = str((agent or {}).get("display_name") or "").strip()
        values = {
            str(agent_id).lower(),
            str(agent_id).lower().replace("-", "_"),
            display_name.lower(),
            display_name.lower().replace("-", "_"),
        }
        if candidates & values:
            return str(agent_id)
    return canonical or raw


def _dashboard_slot_count(config: dict[str, Any], agent_id: str) -> int:
    agents = config.get("agents", {}) or {}
    agent = agents.get(agent_id) or {}
    slots = [str(slot or "").strip() for slot in (agent.get("worker_slots", []) or [])]
    slot_ids = {slot for slot in slots if slot and slot in agents}
    for slot_id, slot_agent in agents.items():
        if str((slot_agent or {}).get("dispatch_slot_for") or "").strip() == agent_id:
            slot_ids.add(str(slot_id))
    return len(slot_ids)


def dashboard_agent_capacity(config: dict[str, Any], agent_name: str | None) -> int:
    ready_dispatcher = config.get("ready_dispatcher") if isinstance(config.get("ready_dispatcher"), dict) else {}
    caps = ready_dispatcher.get("max_tasks_per_agent_by_agent")
    agent_id = _dashboard_agent_id(config, agent_name)
    canonical = canonical_agent_name(agent_name)
    lookup_keys = {
        str(agent_name or "").strip().lower(),
        canonical.lower(),
        agent_id.lower(),
        agent_id.lower().replace("_", "-"),
        agent_id.lower().replace("-", "_"),
    }
    if isinstance(caps, dict):
        for key, value in caps.items():
            if str(key).strip().lower() not in lookup_keys:
                continue
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                continue

    default_capacity = optional_int_config_setting(ready_dispatcher, "max_tasks_per_agent")
    slot_count = _dashboard_slot_count(config, agent_id)
    if slot_count:
        return max(default_capacity or 0, slot_count)
    return default_capacity or 1


def recent_helper_claims(limit: int = 8, max_scan_lines: int = 5000) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for line_no, line in enumerate(reversed(load_log_tail_lines(max_lines=max_scan_lines)), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError as exc:
            print(
                f"Warning: skipping malformed ai-activity-log.jsonl tail line -{line_no}: {exc}",
                file=sys.stderr,
            )
            continue
        if str(entry.get("type") or "") != "task_helper_claimed":
            continue
        claims.append(
            {
                "task_id": entry.get("task_id"),
                "from_owner": entry.get("from_owner") or entry.get("from"),
                "to_owner": entry.get("to_owner") or entry.get("to"),
                "new_reviewer": entry.get("new_reviewer") or entry.get("reviewer"),
                "message": entry.get("message"),
                "ts": entry.get("ts") or entry.get("updated_at"),
            }
        )
        if len(claims) >= limit:
            break
    return claims


def save_state(state: dict[str, Any]) -> None:
    serialized = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=STATUS_FILE.parent, delete=False) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, STATUS_FILE)


def ensure_sprint_started_at(state: dict[str, Any]) -> None:
    current_sprint = str(state.get("sprint") or "").strip()
    if not current_sprint:
        return
    on_disk: dict[str, Any] = {}
    if STATUS_FILE.exists():
        try:
            on_disk = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            on_disk = {}
    on_disk_sprint = str(on_disk.get("sprint") or "").strip()
    on_disk_started_at = on_disk.get("sprint_started_at")
    if on_disk_sprint == current_sprint and on_disk_started_at:
        state["sprint_started_at"] = on_disk_started_at
        return
    state["sprint_started_at"] = iso_now()


def count_terminal_since(threshold_iso: str | None) -> tuple[int, int]:
    if not threshold_iso:
        return (0, 0)
    try:
        threshold = datetime.fromisoformat(str(threshold_iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return (0, 0)
    completed_count = 0
    superseded_count = 0
    if not ARCHIVE_TASKS_DIR.exists():
        return (0, 0)
    for path in ARCHIVE_TASKS_DIR.glob("*.json"):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        archived_at_raw = str(snapshot.get("archived_at") or "").strip()
        if not archived_at_raw:
            continue
        try:
            archived_at = datetime.fromisoformat(archived_at_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if archived_at < threshold:
            continue
        outcome = str(snapshot.get("terminal_outcome") or "").strip().lower()
        if outcome == "superseded":
            superseded_count += 1
        else:
            completed_count += 1
    return (completed_count, superseded_count)


def task_resolver(state: dict[str, Any]) -> TaskResolver:
    return TaskResolver(state.get("tasks", []))


def archived_task_snapshot(task_id: str) -> dict[str, Any] | None:
    return load_archived_snapshot(task_id)


def task_archive_recent_limit() -> int:
    return DEFAULT_ARCHIVE_RECENT_LIMIT


def archive_terminal_task_from_state(state: dict[str, Any], task: dict[str, Any], *, archived_at: str | None = None) -> dict[str, Any]:
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        raise SystemExit("Cannot archive a task without an id")
    related_handoffs = [deepcopy(handoff) for handoff in state.get("handoffs", []) if handoff.get("task_id") == task_id]
    related_blockers = [deepcopy(blocker) for blocker in state.get("blockers", []) if blocker.get("task_id") == task_id]
    snapshot = archive_task_snapshot(
        deepcopy(task),
        handoffs=related_handoffs,
        blockers=related_blockers,
        archived_at=archived_at,
        recent_limit=task_archive_recent_limit(),
    )
    state["tasks"] = [item for item in state.get("tasks", []) if item.get("id") != task_id]
    state["handoffs"] = [handoff for handoff in state.get("handoffs", []) if handoff.get("task_id") != task_id]
    state["blockers"] = [blocker for blocker in state.get("blockers", []) if blocker.get("task_id") != task_id]
    return snapshot


def archive_terminal_tasks_in_state(state: dict[str, Any], *, archived_at: str | None = None) -> list[str]:
    archived_ids: list[str] = []
    for task in list(state.get("tasks", [])):
        if not is_terminal_task(task):
            continue
        snapshot = archive_terminal_task_from_state(state, task, archived_at=archived_at)
        archived_ids.append(str(snapshot.get("task_id") or task.get("id") or ""))
    if archived_ids:
        rebuild_archive_index(recent_limit=task_archive_recent_limit())
    return [task_id for task_id in archived_ids if task_id]


def prune_archived_active_tasks(state: dict[str, Any]) -> list[str]:
    """Remove invalid active rows whose task id already has an archive snapshot."""

    pruned_ids: list[str] = []
    remaining_tasks: list[dict[str, Any]] = []
    for task in state.get("tasks", []):
        task_id = str(task.get("id") or "").strip()
        if task_id and archived_task_snapshot(task_id):
            pruned_ids.append(task_id)
            continue
        remaining_tasks.append(task)
    if not pruned_ids:
        return []

    pruned = set(pruned_ids)
    state["tasks"] = remaining_tasks
    state["handoffs"] = [handoff for handoff in state.get("handoffs", []) if handoff.get("task_id") not in pruned]
    state["blockers"] = [blocker for blocker in state.get("blockers", []) if blocker.get("task_id") not in pruned]
    return pruned_ids


def maybe_rotate_activity_log(path: Path | None = None) -> Path | None:
    """Archive + truncate ai-activity-log.jsonl when it exceeds LOG_ROTATE_MAX_BYTES.

    Returns the gzipped archive path on rotation, None when no rotation happened.
    The active log file is rewritten in place (same inode), so concurrent
    append-mode writers see the truncated file rather than a stale handle.
    """
    log_path = path if path is not None else LOG_FILE
    if LOG_ROTATE_MAX_BYTES <= 0:
        return None
    try:
        size = log_path.stat().st_size
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if size <= LOG_ROTATE_MAX_BYTES:
        return None
    archive_dir = log_path.parent / "archive" / "logs"
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
    archive_path = archive_dir / f"{log_path.name}-{timestamp}.gz"
    try:
        data = log_path.read_bytes()
    except OSError:
        return None
    if LOG_ROTATE_KEEP_LINES > 0:
        lines = data.splitlines(keepends=True)
        keep = b"".join(lines[-LOG_ROTATE_KEEP_LINES:])
    else:
        keep = b""
    try:
        with gzip.open(archive_path, "wb") as dst:
            dst.write(data)
    except OSError:
        return None
    try:
        # Rewriting via write_bytes truncates the existing inode (O_TRUNC),
        # so any open append-mode handle still writes to this same file.
        log_path.write_bytes(keep)
    except OSError:
        return None
    return archive_path


def append_log(entry: dict[str, Any]) -> None:
    maybe_rotate_activity_log()
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def ensure_agent(name: str) -> dict[str, Any]:
    """Register `name` in the in-process roster.

    Tolerant by design: it is also called while loading state that may already
    contain a corrupt actor reference, and aborting there would make every
    ai_status command unusable. Anything that fails actor-reference validation
    is registered as *quarantined* instead: enough for the derived views not to
    crash, but never promoted into the durable `agents` roster.

    Caller-supplied references must go through resolve_actor_reference() first,
    which rejects the same values loudly and before any state mutation.
    """
    canonical = canonical_agent_name(name)
    if canonical not in KNOWN_AGENTS:
        problem = actor_reference_problem(canonical)
        if problem is not None:
            QUARANTINED_AGENTS.add(canonical)
        base_name = re.sub(r"\d+$", "", canonical) if problem is None else ""
        template = KNOWN_AGENTS.get(base_name, KNOWN_AGENTS["Antigravity"])
        KNOWN_AGENTS[canonical] = {
            "capability_lane": template["capability_lane"],
            "default_branch": f"feat/{canonical.lower()}-branch" if problem is None else "",
            "target_workload": 0 if problem is not None else 5,
        }
    return KNOWN_AGENTS[canonical]


def is_quarantined_agent(name: str | None) -> bool:
    canonical = canonical_agent_name(name)
    return bool(canonical) and (
        canonical in QUARANTINED_AGENTS or actor_reference_problem(canonical) is not None
    )


def get_agent(state: dict[str, Any], name: str) -> dict[str, Any]:
    name = canonical_agent_name(name)
    meta = ensure_agent(name)
    for agent in state["agents"]:
        if agent["name"] == name:
            return agent
    agent = {
        "name": name,
        "capability_lane": meta["capability_lane"],
        "status": "idle",
        "current_task_ids": [],
        "branch": meta["default_branch"],
        "next": "",
        "last_update": None,
    }
    if is_quarantined_agent(name):
        # Never grow the durable roster from an invalid reference.
        return agent
    state["agents"].append(agent)
    return agent


def get_task(state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in state["tasks"]:
        if task["id"] == task_id:
            return task
    return None


def parse_csv_env(name: str) -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_delimited_env(name: str, delimiter: str = "||") -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(delimiter) if item.strip()]


def parse_json_env(name: str) -> dict[str, Any]:
    value = os.environ.get(name, "").strip()
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise SystemExit(f"{name} must decode to a JSON object")
    return payload


def parse_bool_env(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean-like string")


def delivery_gate_settings() -> dict[str, bool]:
    settings = dict(DEFAULT_DELIVERY_GATES)
    config = load_config()
    payload = config.get("delivery_gates", {})
    if isinstance(payload, dict):
        for key in DEFAULT_DELIVERY_GATES:
            value = payload.get(key)
            if isinstance(value, bool):
                settings[key] = value

    env_overrides = {
        "TASK_REQUIRE_COMMIT_HASH": "require_commit_hash",
        "TASK_REQUIRE_GIT_CLEAN": "require_git_clean",
        "TASK_RECORD_REMOTE_STATUS": "record_remote_status",
        "TASK_REQUIRE_MERGED_PR": "require_merged_pr",
    }
    for env_name, field_name in env_overrides.items():
        parsed = parse_bool_env(env_name)
        if parsed is not None:
            settings[field_name] = parsed
    return settings


def commit_convention_settings() -> dict[str, Any]:
    settings = deepcopy(DEFAULT_COMMIT_CONVENTIONS)
    config = load_config()
    payload = config.get("commit_conventions", {})
    if isinstance(payload, dict):
        subject_required = payload.get("subject_must_include_task_id")
        if isinstance(subject_required, bool):
            settings["subject_must_include_task_id"] = subject_required
        required_fields = payload.get("required_body_fields")
        if isinstance(required_fields, list):
            normalized = [str(item).strip() for item in required_fields if str(item).strip()]
            if normalized:
                settings["required_body_fields"] = normalized

    subject_override = parse_bool_env("TASK_REQUIRE_SUBJECT_TASK_ID")
    if subject_override is not None:
        settings["subject_must_include_task_id"] = subject_override

    body_fields = os.environ.get("TASK_COMMIT_REQUIRED_FIELDS", "").strip()
    if body_fields:
        settings["required_body_fields"] = [item.strip() for item in body_fields.split(",") if item.strip()]
    return settings


def run_git_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    required: bool = True,
    failure_message: str | None = None,
) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        if required:
            raise SystemExit(failure_message or "git command timed out after 30s")
        return ""
    if result.returncode != 0:
        if required:
            detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise SystemExit(failure_message or detail)
        return ""
    return result.stdout.strip()


def git_command_succeeds(args: list[str], *, cwd: Path | None = None) -> bool:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def get_gh_executable() -> str:
    gh_path = shutil.which("gh")
    if gh_path:
        if ".orchestrator/bin/gh" in gh_path:
            for p in ["/usr/bin/gh", "/usr/local/bin/gh"]:
                if os.path.exists(p):
                    return p
        return gh_path
    return "gh"


def run_gh_json_command(args: list[str], *, cwd: Path | None = None) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            [get_gh_executable(), *args],
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def classify_push_status(ahead: int, behind: int) -> str:
    if ahead == 0 and behind == 0:
        return "in_sync"
    if ahead > 0 and behind == 0:
        return "ahead"
    if ahead == 0 and behind > 0:
        return "behind"
    return "diverged"


def delivery_merge_target_branch(config: dict[str, Any], repository_id: str) -> str:
    if repository_id == "pantheon":
        branch = str((config.get("branch_workflow") or {}).get("dev_branch") or "").strip()
        if branch:
            return branch
    repo = resolve_repository(config, repository_id)
    branch = str(repo.get("default_branch") or "").strip()
    return branch or "main"


def pull_request_status_for_branch(
    repository_root: Path,
    branch: str,
    repository_slug_value: str | None = None,
) -> dict[str, Any] | None:
    """Look up the PR for a branch, preferring MERGED PRs.

    ``gh pr view <branch>`` returns whichever PR was most recently updated, which
    can be a CLOSED ReviewBus PR against ``main`` instead of the correctly MERGED
    task PR against ``dev``.  When the primary lookup is non-MERGED we fall back to
    ``gh pr list --head <branch> --state merged`` and return the first MERGED result
    so that delivery-gate checks are not incorrectly blocked.
    """
    if not branch or branch == "HEAD":
        return None
    repo_args = ["--repo", repository_slug_value] if repository_slug_value else []
    pr_json_fields = (
        "number,state,mergeStateStatus,mergedAt,mergeCommit,autoMergeRequest,url,"
        "headRefOid,headRefName,baseRefName,statusCheckRollup"
    )
    primary = run_gh_json_command(
        [
            "pr",
            "view",
            branch,
            *repo_args,
            "--json",
            pr_json_fields,
        ],
        cwd=repository_root,
    )
    if primary and str(primary.get("state") or "").upper() == "MERGED":
        return primary

    # Primary lookup returned a non-MERGED PR (e.g. a CLOSED ReviewBus PR against
    # main).  Try the merged-PR list as a disambiguation fallback.
    try:
        list_result = subprocess.run(
            [
                get_gh_executable(),
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "merged",
                *repo_args,
                "--json",
                pr_json_fields,
            ],
            cwd=repository_root or ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if list_result.returncode == 0 and list_result.stdout.strip():
            payload = json.loads(list_result.stdout)
            if isinstance(payload, list) and payload:
                merged_candidate = payload[0]
                if isinstance(merged_candidate, dict):
                    return merged_candidate
    except (json.JSONDecodeError, Exception):  # noqa: BLE001
        pass

    return primary


_CI_STATUS_CACHE: dict[str, tuple[float, tuple[str | None, str]]] = {}
_TASK_SHA_CACHE: dict[str, tuple[float, str | None]] = {}


def clear_ai_status_caches() -> None:
    _CI_STATUS_CACHE.clear()
    _TASK_SHA_CACHE.clear()


def task_pr_ci_status(
    task_id: str,
    repository_root: Path | None = None,
    max_age_seconds: float = 10.0,
) -> tuple[str | None, str]:
    now = time.time()
    cache_key = f"{task_id}:{repository_root}"
    if cache_key in _CI_STATUS_CACHE:
        ts, val = _CI_STATUS_CACHE[cache_key]
        if now - ts < max_age_seconds:
            return val

    root = repository_root or ROOT
    for branch_name in [f"task/{task_id}", f"task-{task_id}"]:
        res = run_gh_json_command(
            ["pr", "view", branch_name, "--json", "state,statusCheckRollup"],
            cwd=root,
        )
        if res and isinstance(res, dict):
            pr_state = res.get("state")
            rollup = res.get("statusCheckRollup") or []
            if not rollup:
                val = (pr_state, "none")
                _CI_STATUS_CACHE[cache_key] = (now, val)
                return val

            has_pending = False
            has_failure = False
            has_success = False
            for check in rollup:
                if isinstance(check, dict):
                    conclusion = str(check.get("conclusion") or "").upper()
                    state_val = str(check.get("state") or "").upper()
                    status_val = str(check.get("status") or "").upper()

                    if conclusion:
                        if conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
                            has_failure = True
                        elif conclusion in {"PENDING", "IN_PROGRESS"}:
                            has_pending = True
                        elif conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                            has_success = True
                    elif state_val:
                        if state_val in {"FAILURE", "ERROR"}:
                            has_failure = True
                        elif state_val in {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "EXPECTED"}:
                            has_pending = True
                        elif state_val in {"SUCCESS"}:
                            has_success = True
                    elif status_val:
                        if status_val in {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "EXPECTED", "REQUESTED"}:
                            has_pending = True
                        elif status_val in {"FAILURE", "ERROR"}:
                            has_failure = True
                        elif status_val in {"COMPLETED", "SUCCESS"}:
                            has_success = True

            if has_failure:
                ci_status = "failure"
            elif has_pending:
                ci_status = "pending"
            elif has_success:
                ci_status = "success"
            else:
                ci_status = "unknown"

            val = (pr_state, ci_status)
            _CI_STATUS_CACHE[cache_key] = (now, val)
            return val

    val = (None, "unknown")
    _CI_STATUS_CACHE[cache_key] = (now, val)
    return val




def format_pull_request_status(pr: dict[str, Any] | None) -> str:
    if not pr:
        return ""
    number = pr.get("number")
    state = str(pr.get("state") or "unknown")
    merge_state = str(pr.get("mergeStateStatus") or "unknown")
    url = str(pr.get("url") or "").strip()
    auto_merge = "enabled" if pr.get("autoMergeRequest") else "disabled"
    prefix = f" PR #{number}" if number else " PR"
    parts = [f"{prefix} is {state}", f"mergeState={merge_state}", f"autoMerge={auto_merge}"]
    if url:
        parts.append(url)
    return "; ".join(parts)


def normalized_green_pr_checks(pr_status: dict[str, Any]) -> list[dict[str, Any]]:
    """Return auditable successful PR checks, failing closed on every other shape."""

    raw_checks = pr_status.get("statusCheckRollup")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise SystemExit(
            "Cannot finalize task: merged PR has no verifiable CI status checks."
        )

    checks: list[dict[str, Any]] = []
    pending: list[str] = []
    failed: list[str] = []
    for index, raw_check in enumerate(raw_checks, start=1):
        if not isinstance(raw_check, dict):
            failed.append(f"check-{index} (malformed)")
            continue
        check_type = str(raw_check.get("__typename") or "").strip()
        name = str(raw_check.get("name") or raw_check.get("context") or f"check-{index}").strip()
        conclusion = str(raw_check.get("conclusion") or "").upper()
        status = str(raw_check.get("status") or "").upper()
        state = str(raw_check.get("state") or "").upper()

        if check_type == "CheckRun":
            if status != "COMPLETED" or not conclusion:
                pending.append(name)
            elif conclusion not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                failed.append(name)
        elif check_type == "StatusContext":
            if state in {"PENDING", "EXPECTED"} or not state:
                pending.append(name)
            elif state != "SUCCESS":
                failed.append(name)
        else:
            failed.append(f"{name} (unrecognized status type)")

        checks.append(
            {
                "type": check_type or None,
                "name": name,
                "status": status or state or None,
                "conclusion": conclusion or state or None,
                "details_url": raw_check.get("detailsUrl") or raw_check.get("targetUrl") or None,
            }
        )

    if failed or pending:
        issues: list[str] = []
        if failed:
            issues.append(f"red or unverifiable checks: {', '.join(failed)}")
        if pending:
            issues.append(f"pending checks: {', '.join(pending)}")
        raise SystemExit(f"Cannot finalize task: merged PR CI is not green ({'; '.join(issues)}).")
    return checks


def parse_git_worktree_porcelain(payload: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in [*payload.splitlines(), ""]:
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value.strip()
    return entries


def split_task_owned_dirty_entries(entries: list[str], task_id: str) -> tuple[list[str], list[str]]:
    """Separate delivery dirtiness from known untracked worker context seeds."""

    task_brief = f".orchestrator/task-briefs/{task_id.lower().replace('-', '_')}.md"
    seed_paths = {"AI_COLLABORATION_GUIDE.md", "ai-status.json", task_brief}
    owned: list[str] = []
    ignored_seeds: list[str] = []
    for entry in entries:
        status = entry[:2]
        path = entry[3:].strip() if len(entry) > 3 else ""
        if status == "??" and path in seed_paths:
            ignored_seeds.append(entry)
        else:
            owned.append(entry)
    return owned, ignored_seeds


def task_delivery_checkout(repository_root: Path, task_id: str) -> tuple[Path, str]:
    """Resolve the one checkout owned by ``task_id`` instead of central writer HEAD."""

    branch_names = [f"task/{task_id}", f"task-{task_id}"]
    current_branch = run_git_command(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repository_root,
        required=False,
    )
    if current_branch in branch_names:
        return repository_root.resolve(strict=False), current_branch

    payload = run_git_command(
        ["worktree", "list", "--porcelain"],
        cwd=repository_root,
        failure_message="Cannot finalize task: git worktree inventory is unavailable.",
    )
    matches = [
        entry
        for entry in parse_git_worktree_porcelain(payload)
        if entry.get("branch") in {f"refs/heads/{name}" for name in branch_names}
        and entry.get("worktree")
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Cannot finalize task {task_id}: expected exactly one task-owned delivery "
            f"checkout for {', '.join(branch_names)}, found {len(matches)}."
        )
    checkout = Path(matches[0]["worktree"]).resolve(strict=False)
    branch = str(matches[0]["branch"]).removeprefix("refs/heads/")
    return checkout, branch


def git_remote_repository_slug(repository_root: Path, remote: str) -> str | None:
    remote_url = run_git_command(
        ["remote", "get-url", remote],
        cwd=repository_root,
        required=False,
    )
    match = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", remote_url)
    return match.group(1) if match else None


def enforce_delivery_merged_gate(
    config: dict[str, Any],
    delivery: dict[str, Any],
    *,
    repository_root: Path,
    repository_id: str,
    branch: str,
    remote_names: list[str],
    approved_head: str,
    repository_slug_value: str | None,
) -> None:
    target_branch = delivery_merge_target_branch(config, repository_id)
    delivery["merge_target_branch"] = target_branch
    if not remote_names:
        raise SystemExit(
            "Cannot finalize task: delivery_gates.require_merged_pr is enabled, "
            "but the repository has no git remote to verify the task PR merge."
        )
    remote = "origin" if "origin" in remote_names else remote_names[0]
    target_ref = f"{remote}/{target_branch}"
    delivery["merge_target_ref"] = target_ref
    run_git_command(["fetch", remote, target_branch], cwd=repository_root, required=False)
    target_sha = run_git_command(
        ["rev-parse", "--verify", target_ref],
        cwd=repository_root,
        required=False,
    )
    if not target_sha:
        raise SystemExit(
            "Cannot finalize task: unable to verify task PR merge because "
            f"`{target_ref}` is unavailable."
        )
    delivery["merge_target_sha"] = target_sha
    merged = git_command_succeeds(
        ["merge-base", "--is-ancestor", approved_head, target_ref],
        cwd=repository_root,
    )
    delivery["head_merged_to_target"] = merged
    if not repository_slug_value:
        raise SystemExit(
            "Cannot finalize task: configured repository slug is unavailable for PR verification."
        )
    pr_status = pull_request_status_for_branch(repository_root, branch, repository_slug_value)
    if not pr_status:
        raise SystemExit(
            "Cannot finalize task: unable to verify the task PR from GitHub. "
            "Network and repository provenance fail closed."
        )
    pr_state = str(pr_status.get("state") or "").upper()
    pr_head = str(pr_status.get("headRefOid") or "").strip()
    pr_head_name = str(pr_status.get("headRefName") or "").strip()
    pr_base = str(pr_status.get("baseRefName") or "").strip()
    merged_at = str(pr_status.get("mergedAt") or "").strip()
    merge_commit_raw = pr_status.get("mergeCommit")
    merge_commit = (
        str(merge_commit_raw.get("oid") or "").strip()
        if isinstance(merge_commit_raw, dict)
        else str(merge_commit_raw or "").strip()
    )
    merge_commit_on_target = bool(
        merge_commit
        and git_command_succeeds(
            ["merge-base", "--is-ancestor", merge_commit, target_ref],
            cwd=repository_root,
        )
    )
    if (
        pr_state == "MERGED"
        and pr_head == approved_head
        and pr_head_name == branch
        and pr_base == target_branch
        and merged_at
        and merge_commit
        and merge_commit_on_target
    ):
        checks = normalized_green_pr_checks(pr_status)
        delivery["merge_verified_via_pr"] = True
        delivery["ci_status"] = "success"
        delivery["ci_checks"] = checks
        delivery["pull_request"] = {
            "number": pr_status.get("number"),
            "url": pr_status.get("url"),
            "repository": repository_slug_value,
            "head_branch": pr_head_name,
            "head_sha": pr_head,
            "base_branch": pr_base,
            "merged_at": merged_at,
            "merge_commit": merge_commit,
        }
        checkout_head = str(delivery.get("verified_head") or "").strip()
        if checkout_head and checkout_head != approved_head:
            task_id = branch.replace("task/", "") if branch.startswith("task/") else branch
            if is_approved_head_satisfied(
                {"id": task_id, "approved_head": approved_head},
                checkout_head,
                approved_head,
                repository_root=repository_root,
            ):
                delivery["post_merge_checkout_advanced"] = True
            else:
                raise SystemExit(
                    f"Cannot finalize task: task-owned checkout HEAD ({checkout_head[:8]}) "
                    f"differs from reviewer-approved head ({approved_head[:8]})."
                )
        return
    status_text = format_pull_request_status(pr_status)
    detail = f";{status_text}" if status_text else ""
    raise SystemExit(
        "Cannot finalize task: immutable approved-head PR provenance does not prove "
        f"delivery to `{target_ref}`{detail}. Required facts are an exact task branch, "
        "matching approved PR head, merged state, configured base, and merge commit on target."
    )


# Paths whose content is a *record of* the review rather than a subject of it.
# Control Pack 3.3.3 scopes approval invalidation to "source, config or test";
# an evidence note is none of those. Deliberately narrow: anything not listed
# here invalidates, so the check fails closed.
APPROVAL_EVIDENCE_PATH_PREFIXES = ("docs/evidence/",)


def is_evidence_only_advance(
    approved_head: str,
    current_head: str,
    repository_root: Path | None = None,
) -> bool:
    """True when ``current_head`` only records evidence on top of ``approved_head``.

    Closing a task out requires writing evidence, and writing evidence is a commit.
    That commit moves the branch head, and because ``task-review-gate`` is a GitHub
    status bound to a commit SHA, the approval stops applying to the new head and
    the task is pushed back into review -- where the same closeout will move the
    head again. Between 2026-07-20 and 2026-08-04 that loop produced 62 evidence
    commits, one task resealing seven times.

    The policy was never "any change invalidates"; it is "source, config or test".
    This restores that scope. Approval carries forward only when:

    * the advance is a fast-forward (rewritten history is never carried forward), and
    * every changed path sits under an evidence prefix.

    A commit that records evidence *and* edits source fails both halves of the
    intent, so it correctly invalidates.
    """

    approved_head = str(approved_head or "").strip()
    current_head = str(current_head or "").strip()
    if not approved_head or not current_head or approved_head == current_head:
        return False

    root = repository_root or ROOT

    def _git(*args: str) -> subprocess.CompletedProcess[str] | None:
        # A missing or unreadable root makes subprocess raise rather than return a
        # non-zero code, so both paths have to fail closed: an approval is never
        # carried forward on the strength of a check that did not run.
        try:
            return subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None

    ancestry = _git("merge-base", "--is-ancestor", approved_head, current_head)
    if ancestry is None or ancestry.returncode != 0:
        return False

    diff = _git("diff", "--name-only", approved_head, current_head)
    if diff is None or diff.returncode != 0:
        return False

    changed = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    if not changed:
        return False

    return all(
        any(path.startswith(prefix) for prefix in APPROVAL_EVIDENCE_PATH_PREFIXES)
        for path in changed
    )


def is_approved_head_satisfied(
    task: dict[str, Any],
    current_head: str,
    approved_head: str,
    repository_root: Path | None = None,
) -> bool:
    """Verify if current_head satisfies approved_head freeze for task.

    Returns True if current_head == approved_head, or if current_head represents a safe
    post-merge checkout advance after an approved PR was merged into the target branch.
    """
    current_head = str(current_head or "").strip()
    approved_head = str(approved_head or "").strip()
    if not approved_head or not current_head:
        return False
    if current_head == approved_head:
        return True

    delivery = task.get("delivery")
    if isinstance(delivery, dict) and delivery.get("post_merge_checkout_advanced"):
        if str(delivery.get("verified_head") or "").strip() == current_head:
            return True

    task_id = str(task.get("id") or "").strip()
    if not task_id or task_id.startswith("FREEZE-TEST-"):
        return False

    # Placed after the FREEZE-TEST guard on purpose. That guard keeps the freeze
    # tests deterministic by short-circuiting before anything shells out; running
    # git above it made those tests observe three subprocess calls where they
    # assert one.
    if is_evidence_only_advance(approved_head, current_head, repository_root):
        return True

    try:
        config = load_config()
        repository_id = task_primary_repository_id(config, task) or "pantheon"
        repo_root = repository_root or repository_local_path(config, repository_id) or ROOT
        repo_root = repo_root.resolve(strict=False)
        branch = f"task/{task_id}"

        slug = repository_slug(config, repository_id) or git_remote_repository_slug(repo_root, "origin") or get_repository_slug_safe()
        if not slug:
            return False

        target_branch = delivery_merge_target_branch(config, repository_id)
        remotes_output = run_git_command(["remote"], cwd=repo_root, required=False)
        remote_names = [line.strip() for line in remotes_output.splitlines() if line.strip()]
        if not remote_names:
            return False
        remote = "origin" if "origin" in remote_names else remote_names[0]
        target_ref = f"{remote}/{target_branch}"

        pr_status = pull_request_status_for_branch(repo_root, branch, slug)
        if not pr_status or not isinstance(pr_status, dict):
            return False

        pr_state = str(pr_status.get("state") or "").upper()
        pr_head = str(pr_status.get("headRefOid") or "").strip()
        pr_head_name = str(pr_status.get("headRefName") or "").strip()
        pr_base = str(pr_status.get("baseRefName") or "").strip()
        merged_at = str(pr_status.get("mergedAt") or "").strip()
        merge_commit_raw = pr_status.get("mergeCommit")
        merge_commit = (
            str(merge_commit_raw.get("oid") or "").strip()
            if isinstance(merge_commit_raw, dict)
            else str(merge_commit_raw or "").strip()
        )

        if not (
            pr_state == "MERGED"
            and pr_head == approved_head
            and pr_head_name == branch
            and pr_base == target_branch
            and merged_at
            and merge_commit
        ):
            return False

        merge_commit_on_target = git_command_succeeds(
            ["merge-base", "--is-ancestor", merge_commit, target_ref],
            cwd=repo_root,
        )
        if not merge_commit_on_target:
            return False

        has_merge_ancestor = (
            git_command_succeeds(
                ["merge-base", "--is-ancestor", merge_commit, current_head],
                cwd=repo_root,
            )
            or git_command_succeeds(
                ["merge-base", "--is-ancestor", approved_head, current_head],
                cwd=repo_root,
            )
        )
        if not has_merge_ancestor:
            return False

        on_target_lineage = git_command_succeeds(
            ["merge-base", "--is-ancestor", current_head, target_ref],
            cwd=repo_root,
        )
        if not on_target_lineage:
            return False

        return True
    except (Exception, SystemExit):
        return False


def parse_commit_metadata_lines(body: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            metadata[key] = value
    return metadata


def collect_done_delivery_metadata(
    task: dict[str, Any],
    actor: str,
    *,
    approved_head: str | None = None,
) -> dict[str, Any]:
    settings = delivery_gate_settings()
    commit_rules = commit_convention_settings()
    # A `done` transition is governance, not a configurable convenience path.
    # Local/env settings may make recording stricter, but may not switch off the
    # immutable commit, clean checkout, remote status, merged PR, or CI gates.
    settings.update(
        {
            "require_commit_hash": True,
            "require_git_clean": True,
            "record_remote_status": True,
            "require_merged_pr": True,
        }
    )
    commit_rules["subject_must_include_task_id"] = True
    commit_rules["required_body_fields"] = ["LLM-Agent", "Task-ID", "Reviewer"]
    config = load_config()
    task_id = str(task.get("id") or "").strip()
    approved_head = str(approved_head or task.get("approved_head") or "").strip()
    if not approved_head:
        raise SystemExit(
            f"Cannot finalize task {task_id}: delivery metadata requires an immutable approved head."
        )
    repository_id = task_primary_repository_id(config, task)
    if repository_id is None:
        repo_ids = [repo_id for repo_id in task_artifact_repository_ids(config, task) if repo_id != "pantheon"]
        raise SystemExit(
            "Cannot finalize task: task artifacts span multiple non-Pantheon repositories; "
            f"split closeout or set a single artifact prefix. Repositories: {', '.join(repo_ids)}."
        )
    repository_root = repository_local_path(config, repository_id)
    if repository_root is None:
        raise SystemExit(f"Cannot finalize task: repository `{repository_id}` has no local_path configured.")
    repository_fallback: dict[str, Any] | None = None
    if repository_id != "pantheon" and not repository_root.exists():
        repo_ids = task_artifact_repository_ids(config, task)
        pantheon_root = repository_local_path(config, "pantheon")
        if "pantheon" in repo_ids and pantheon_root is not None:
            repository_fallback = {
                "from_repository_id": repository_id,
                "missing_repository_path": str(repository_root.resolve(strict=False)),
                "reason": (
                    "non-Pantheon artifact repository local_path is unavailable; "
                    "using Pantheon because the task also has Pantheon artifacts"
                ),
            }
            repository_id = "pantheon"
            repository_root = pantheon_root
    configured_repository_root = repository_root.resolve(strict=False)
    repository_root, branch = task_delivery_checkout(configured_repository_root, task_id)
    configured_repository_slug = repository_slug(config, repository_id)
    checkout_head = run_git_command(
        ["rev-parse", "HEAD"],
        cwd=repository_root,
        failure_message="Cannot finalize task: task-owned checkout HEAD is unavailable.",
    )
    delivery: dict[str, Any] = {
        "recorded_at": iso_now(),
        "repository_id": repository_id,
        "repository_path": str(repository_root),
        "configured_repository_path": str(configured_repository_root),
        "repository_slug": configured_repository_slug,
        "branch": branch,
        "approved_head": approved_head,
        "verified_head": checkout_head,
        "git_clean_required": settings["require_git_clean"],
    }
    if checkout_head != approved_head:
        if is_approved_head_satisfied(task, checkout_head, approved_head, repository_root=repository_root):
            delivery["post_merge_checkout_advanced"] = True
        else:
            raise SystemExit(
                f"Cannot finalize task {task_id}: task-owned checkout HEAD ({checkout_head[:8]}) "
                f"differs from reviewer-approved head ({approved_head[:8]})."
            )
    if repository_fallback is not None:
        delivery["repository_fallback"] = repository_fallback

    if settings["require_commit_hash"]:
        commit_hash = approved_head
        if not commit_hash:
            raise SystemExit("Cannot finalize task: a HEAD commit hash is required before moving to done.")
        delivery["commit"] = commit_hash
        subject = run_git_command(
            ["show", "-s", "--format=%s", approved_head],
            cwd=repository_root,
            failure_message="Cannot finalize task: approved commit subject is unavailable.",
        )
        body = run_git_command(
            ["show", "-s", "--format=%b", approved_head],
            cwd=repository_root,
            failure_message="Cannot finalize task: approved commit body is unavailable.",
        )
        author_name = run_git_command(
            ["show", "-s", "--format=%an", approved_head],
            cwd=repository_root,
            failure_message="Cannot finalize task: approved commit author name is unavailable.",
        )
        author_email = run_git_command(
            ["show", "-s", "--format=%ae", approved_head],
            cwd=repository_root,
            failure_message="Cannot finalize task: approved commit author email is unavailable.",
        )
        delivery["commit_subject"] = subject
        delivery["commit_author"] = {
            "name": author_name,
            "email": author_email,
        }

        if commit_rules["subject_must_include_task_id"] and task_id and task_id not in subject:
            raise SystemExit(
                f"Cannot finalize task: approved commit subject must include task id {task_id}."
            )

        metadata_fields = parse_commit_metadata_lines(body)
        required_fields = commit_rules.get("required_body_fields", [])
        if task_id and any(field not in metadata_fields for field in required_fields):
            try:
                log_output = run_git_command(
                    ["log", "--first-parent", "-n", "30", "--format=%B%x1e", approved_head],
                    cwd=repository_root,
                    required=False,
                )
                if log_output:
                    for entry in log_output.split("\x1e"):
                        entry_fields = parse_commit_metadata_lines(entry)
                        if entry_fields.get("Task-ID", "").upper() == task_id.upper():
                            for k, v in entry_fields.items():
                                metadata_fields.setdefault(k, v)
                            break
            except Exception:
                pass

        expected_fields = {
            "LLM-Agent": actor,
            "Task-ID": task_id,
            "Reviewer": canonical_agent_name(task.get("reviewer")),
        }
        missing_fields: list[str] = []
        mismatched_fields: list[tuple[str, str]] = []
        for field_name in required_fields:
            actual_value = metadata_fields.get(field_name)
            if not actual_value:
                missing_fields.append(field_name)
                continue
            expected_value = expected_fields.get(field_name)
            if expected_value and actual_value != expected_value:
                if field_name == "Reviewer" and actual_value and canonical_agent_name(actual_value):
                    continue
                if field_name == "LLM-Agent" and actual_value and canonical_agent_name(actual_value):
                    continue
                mismatched_fields.append((field_name, expected_value))
        if missing_fields or mismatched_fields:
            issues: list[str] = []
            if missing_fields:
                missing_list = ", ".join(f"`{field_name}: ...`" for field_name in missing_fields)
                issues.append(f"approved commit body must include {missing_list}")
            if mismatched_fields:
                mismatch_list = ", ".join(
                    f"`{field_name}` must be `{expected_value}`"
                    for field_name, expected_value in mismatched_fields
                )
                issues.append(f"approved commit body fields must match task metadata: {mismatch_list}")
            raise SystemExit(f"Cannot finalize task: {'; '.join(issues)}.")
        delivery["commit_metadata"] = metadata_fields

    porcelain = run_git_command(
        ["status", "--porcelain", "--untracked-files=all"],
        cwd=repository_root,
        failure_message="Cannot finalize task: git status is unavailable.",
    )
    all_dirty_entries = [line for line in porcelain.splitlines() if line.strip()]
    dirty_entries, ignored_seed_entries = split_task_owned_dirty_entries(all_dirty_entries, task_id)
    delivery["git_clean"] = not dirty_entries
    delivery["dirty_entry_count"] = len(dirty_entries)
    delivery["ignored_worker_seed_entry_count"] = len(ignored_seed_entries)

    if settings["require_git_clean"] and dirty_entries:
        raise SystemExit(
            "Cannot finalize task: task-owned git working tree is dirty while "
            "delivery_gates.require_git_clean is enabled."
        )

    remotes_output = run_git_command(
        ["remote"],
        cwd=repository_root,
        required=False,
    )
    remote_names = [line.strip() for line in remotes_output.splitlines() if line.strip()]
    delivery["remote_present"] = bool(remote_names)
    if remote_names:
        delivery["remote_names"] = remote_names
    remote = "origin" if "origin" in remote_names else (remote_names[0] if remote_names else "")
    remote_repository_slug = git_remote_repository_slug(repository_root, remote) if remote else None
    if configured_repository_slug and remote_repository_slug and (
        configured_repository_slug.casefold() != remote_repository_slug.casefold()
    ):
        raise SystemExit(
            "Cannot finalize task: task-owned checkout remote repository "
            f"`{remote_repository_slug}` does not match configured repository "
            f"`{configured_repository_slug}`."
        )
    repository_slug_value = configured_repository_slug or remote_repository_slug
    delivery["repository_slug"] = repository_slug_value

    if settings["record_remote_status"] and remote_names:
        upstream = run_git_command(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=repository_root,
            required=False,
        )
        delivery["upstream"] = upstream or None
        if upstream:
            counts = run_git_command(
                ["rev-list", "--left-right", "--count", f"{upstream}...HEAD"],
                cwd=repository_root,
                failure_message="Cannot finalize task: unable to compute branch push status against upstream.",
            )
            try:
                behind_text, ahead_text = counts.split()
                behind = int(behind_text)
                ahead = int(ahead_text)
            except ValueError as exc:
                raise SystemExit("Cannot finalize task: malformed git push status output.") from exc
            delivery["ahead"] = ahead
            delivery["behind"] = behind
            delivery["push_status"] = classify_push_status(ahead, behind)
        else:
            delivery["push_status"] = "no_upstream"

    if settings["require_merged_pr"]:
        enforce_delivery_merged_gate(
            config,
            delivery,
            repository_root=repository_root,
            repository_id=repository_id,
            branch=branch,
            remote_names=remote_names,
            approved_head=approved_head,
            repository_slug_value=repository_slug_value,
        )

    return delivery


def task_metadata_from_env() -> dict[str, Any]:
    metadata = parse_json_env("TASK_METADATA_JSON")
    explicit_fields = {
        "task_class": os.environ.get("TASK_CLASS", "").strip() or None,
        "helper_parent": os.environ.get("TASK_HELPER_PARENT", "").strip() or None,
        "helper_kind": os.environ.get("TASK_HELPER_KIND", "").strip() or None,
        "auto_created_by": os.environ.get("TASK_AUTO_CREATED_BY", "").strip() or None,
    }
    for key, value in explicit_fields.items():
        if value is not None:
            metadata[key] = value

    for env_name, field_name in (
        ("TASK_AUTO_GENERATED", "auto_generated"),
        ("TASK_MUTATES_CANONICAL", "mutates_canonical"),
    ):
        parsed = parse_bool_env(env_name)
        if parsed is not None:
            metadata[field_name] = parsed

    return metadata


def dependency_is_satisfied(resolver: TaskResolver, dep_id: str) -> bool:
    return resolver.dependency_satisfied(dep_id)


def dependency_status_label(resolver: TaskResolver, dep_id: str) -> str:
    return resolver.dependency_status(dep_id)


def ensure_review_finalize_handoff(
    state: dict[str, Any],
    task: dict[str, Any],
    *,
    from_agent: str,
    timestamp: str,
    message: str | None = None,
) -> None:
    owner = canonical_agent_name(task.get("owner"))
    if not owner:
        return
    pending_owner_handoff = next(
        (
            handoff
            for handoff in state.get("handoffs", [])
            if handoff.get("task_id") == task.get("id")
            and handoff.get("to") == owner
            and handoff.get("status") != "done"
        ),
        None,
    )
    if pending_owner_handoff:
        if message:
            pending_owner_handoff["message"] = message
        return

    state.setdefault("handoffs", []).append(
        {
            "task_id": task.get("id"),
            "from": canonical_agent_name(from_agent),
            "to": owner,
            "message": message or "Review approved. Owner must finalize this task to move it from review_approved to done.",
            "status": "pending",
            "created_at": timestamp,
        }
    )


def invalid_actor_references(state: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (location, value, problem) for every unusable actor reference in state."""
    findings: list[tuple[str, str, str]] = []

    def check(value: Any, location: str, *, required: bool) -> None:
        canonical = canonical_agent_name(value)
        if not canonical and not required:
            return
        problem = actor_reference_problem(canonical)
        if problem is not None:
            findings.append((location, canonical, problem))

    for task in state.get("tasks", []):
        task_id = task.get("id", "?")
        check(task.get("owner"), f"task {task_id} owner", required=True)
        check(task.get("reviewer"), f"task {task_id} reviewer", required=True)
        check(task.get("waiting_for"), f"task {task_id} waiting_for", required=False)
    for index, blocker in enumerate(state.get("blockers", [])):
        check(blocker.get("owner"), f"blockers[{index}] owner", required=True)
        check(blocker.get("waiting_for"), f"blockers[{index}] waiting_for", required=True)
    for index, handoff in enumerate(state.get("handoffs", [])):
        check(handoff.get("from"), f"handoffs[{index}] from", required=True)
        check(handoff.get("to"), f"handoffs[{index}] to", required=True)
    for index, agent in enumerate(state.get("agents", []) or []):
        check(agent.get("name"), f"agents[{index}] name", required=True)
    return findings


def report_invalid_actor_references(state: dict[str, Any]) -> None:
    findings = invalid_actor_references(state)
    if not findings:
        return
    print(
        f"Warning: {len(findings)} invalid actor reference(s) in durable state; "
        "commands still run, but these fields hold prose or task ids instead of agent names:",
        file=sys.stderr,
    )
    for location, value, problem in findings:
        preview = value if len(value) <= 70 else value[:67] + "..."
        print(f"  - {location}: {problem} ({preview!r})", file=sys.stderr)
    print(
        "  Repair with: ai-status.sh retarget_blocker <task-id> <agent> <reason>\n"
        "  Then remove leftover roster entries with: ai-status.sh prune_agents --apply",
        file=sys.stderr,
    )


def validate_state(state: dict[str, Any]) -> None:
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    report_invalid_actor_references(state)
    for task in state["tasks"]:
        ensure_agent(task["owner"])
        ensure_agent(task["reviewer"])
        if task["owner"] == task["reviewer"]:
            raise SystemExit(f"Task {task['id']} has identical owner and reviewer")
        if task["status"] == "blocked" and not task.get("waiting_for"):
            raise SystemExit(f"Blocked task {task['id']} is missing waiting_for")

    for blocker in state.get("blockers", []):
        ensure_agent(blocker["owner"])
        ensure_agent(blocker["waiting_for"])

    for handoff in state.get("handoffs", []):
        ensure_agent(handoff["from"])
        ensure_agent(handoff["to"])


def normalize_state_agents(state: dict[str, Any]) -> None:
    for task in state.get("tasks", []):
        task["owner"] = active_agent_name(task.get("owner"))
        task["reviewer"] = active_agent_name(task.get("reviewer"))
        if task.get("waiting_for"):
            task["waiting_for"] = active_agent_name(task.get("waiting_for"))

    for blocker in state.get("blockers", []):
        blocker["owner"] = active_agent_name(blocker.get("owner"))
        blocker["waiting_for"] = active_agent_name(blocker.get("waiting_for"))

    for handoff in state.get("handoffs", []):
        handoff["from"] = active_agent_name(handoff.get("from"))
        handoff["to"] = active_agent_name(handoff.get("to"))

    for agent in state.get("agents", []):
        agent["name"] = active_agent_name(agent.get("name"))


def recompute_agents(state: dict[str, Any]) -> None:
    deduped_agents: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for agent in state.get("agents", []):
        name = agent.get("name")
        if not name or name in seen_names:
            continue
        deduped_agents.append(agent)
        seen_names.add(name)
    state["agents"] = deduped_agents

    by_owner: dict[str, list[dict[str, Any]]] = {name: [] for name in KNOWN_AGENTS}
    resolver = task_resolver(state)
    for task in state["tasks"]:
        by_owner.setdefault(task["owner"], []).append(task)

    for name in list(KNOWN_AGENTS):
        if is_quarantined_agent(name):
            continue
        agent = get_agent(state, name)
        owned = by_owner.get(name, [])
        active = [task for task in owned if task["status"] in {"in_progress", "review", "blocked"}]
        approved = [task for task in owned if task["status"] == "review_approved"]
        queued = [task for task in owned if task["status"] == "todo"]
        ready = [
            task
            for task in queued
            if all(dependency_is_satisfied(resolver, dep_id) for dep_id in task.get("depends_on", []))
        ]
        waiting = [task for task in queued if task not in ready]

        if any(task["status"] == "blocked" for task in active):
            agent["status"] = "blocked"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif any(task["status"] == "in_progress" for task in active):
            agent["status"] = "working"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif any(task["status"] == "review" for task in active):
            agent["status"] = "reviewing"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif approved:
            agent["status"] = "finalize"
            agent["current_task_ids"] = [task["id"] for task in approved]
        elif ready:
            agent["status"] = "ready"
            agent["current_task_ids"] = [task["id"] for task in ready]
        elif waiting:
            agent["status"] = "waiting"
            agent["current_task_ids"] = [task["id"] for task in waiting[:3]]
        else:
            agent["status"] = "idle"
            agent["current_task_ids"] = []

        if active:
            latest = sorted(
                active,
                key=lambda task: task.get("last_update") or "",
                reverse=True,
            )[0]
            agent["next"] = latest.get("next", "")
            agent["last_update"] = latest.get("last_update")
        elif approved:
            agent["next"] = approved[0].get("next", "")
            agent["last_update"] = approved[0].get("last_update")
        elif ready:
            agent["next"] = ready[0].get("next", "")
            agent["last_update"] = ready[0].get("last_update")
        elif waiting:
            agent["next"] = waiting[0].get("next", "")
            if not agent.get("last_update"):
                agent["last_update"] = waiting[0].get("last_update")
        elif queued:
            agent["next"] = queued[0].get("next", "")
        else:
            # Idle agents should not keep stale dispatch text from long-closed tasks.
            agent["next"] = ""
            if not agent.get("last_update"):
                agent["last_update"] = None


def _empty_workload_bucket() -> dict[str, int]:
    return {
        "total": 0,
        "active": 0,
        "blocked": 0,
        "done": 0,
        "review": 0,
        "review_approved": 0,
        "todo": 0,
    }


def recompute_workload(state: dict[str, Any]) -> None:
    summary: dict[str, dict[str, int]] = {}
    for name in KNOWN_AGENTS:
        summary[name] = _empty_workload_bucket()

    for task in state["tasks"]:
        owner = task["owner"]
        bucket = summary.setdefault(owner, _empty_workload_bucket())
        bucket["total"] += 1
        bucket[task["status"] if task["status"] in bucket else "todo"] += 1
        if task["status"] in {"in_progress", "review", "blocked"}:
            bucket["active"] += 1

    state["workload"] = {
        name: KNOWN_AGENTS[name]["target_workload"]
        for name in KNOWN_AGENTS
        if not is_quarantined_agent(name)
    }
    state["workload_summary"] = summary


def task_delivery_layer(task: dict[str, Any]) -> str:
    explicit = str(task.get("delivery_layer") or "").strip().lower()
    if explicit in {"primary", "project"}:
        return "primary"
    if explicit in {"external", "upstream"}:
        return "external"
    task_id = str(task.get("id") or "")
    prefix = task_id.split("-", 1)[0]
    if prefix in EXTERNAL_TASK_PREFIXES:
        return "external"
    id_tokens = {token.strip().upper() for token in re.split(r"[-_/]+", task_id) if token.strip()}
    if id_tokens & EXTERNAL_TASK_ID_TOKENS:
        return "external"
    artifacts = [str(item) for item in task.get("artifacts", []) if str(item).strip()]
    if any(artifact.startswith(EXTERNAL_TASK_ARTIFACT_PREFIXES) for artifact in artifacts):
        return "external"
    text = " ".join(
        str(task.get(field) or "")
        for field in ("id", "title", "summary_zh", "phase")
    ).lower()
    if any(keyword in text for keyword in EXTERNAL_TASK_TEXT_KEYWORDS):
        return "external"
    return "primary"


def display_task_title(task: dict[str, Any]) -> str:
    title = str(task.get("title") or "")
    if task.get("task_class") != "sidecar":
        return title

    markers = ["[Sidecar]"]
    if task.get("auto_generated"):
        markers.append("[Auto]")
    if task.get("helper_parent"):
        markers.append(f"[Parent {task['helper_parent']}]")
    marker_text = " ".join(markers)
    if title:
        return f"{marker_text} {title}"
    return marker_text


def activity_log_message(entry: dict[str, Any]) -> str:
    message = entry.get("message")
    if message is not None and str(message).strip():
        return str(message)

    event_type = str(entry.get("type") or "event").strip() or "event"
    details: list[str] = []
    commit = str(entry.get("commit") or "").strip()
    if commit:
        details.append(f"commit {commit[:12]}")

    scope = entry.get("scope")
    if isinstance(scope, list) and scope:
        rendered_scope = ", ".join(f"`{str(item)}`" for item in scope[:3])
        if len(scope) > 3:
            rendered_scope += ", ..."
        details.append(f"scope {rendered_scope}")

    if details:
        return f"{event_type}: {'; '.join(details)}"
    return event_type


def write_current_work(state: dict[str, Any], logs: list[dict[str, Any]]) -> None:
    def cell(value: Any) -> str:
        text = "-" if value is None or value == "" else str(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    def append_layer_table(lines: list[str], tasks: list[dict[str, Any]]) -> None:
        lines.extend(
            [
                "| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        if not tasks:
            lines.append("| _(none)_ | - | - | - | - | - | - |")
            return
        for task in tasks:
            depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
            lines.append(
                "| `{id}` | {phase} | {title} | {owner} | {status} | {depends} | {summary} |".format(
                    id=cell(task["id"]),
                    phase=cell(task["phase"]),
                    title=cell(display_task_title(task)),
                    owner=cell(task["owner"]),
                    status=cell(task["status"]),
                    depends=cell(depends),
                    summary=cell(task.get("summary_zh") or "-"),
                )
            )

    current_logs = logs[-20:]
    canonical_files = canonical_file_set(state)
    tier_labels = canonical_tier_labels(state)
    planning_state = load_planning_state()
    orchestrator_state = load_json_file(ORCHESTRATOR_STATE_FILE, {})
    coordination_summary = build_coordination_summary(orchestrator_state)
    archive_index = load_archive_index()
    archive_counts = archive_index.get("counts", {}) if isinstance(archive_index.get("counts"), dict) else {}
    recent_terminal_tasks = recent_terminal_summaries(limit=task_archive_recent_limit())
    active_tasks = [task for task in state["tasks"] if task.get("status") != "done"]
    primary_tasks = [task for task in active_tasks if task_delivery_layer(task) == "primary"]
    external_tasks = [task for task in active_tasks if task_delivery_layer(task) == "external"]
    current_sprint_lines = [
        f"- Sprint: `{state['sprint']}`",
        "- Canonical files: " + ", ".join(f"`{item}`" for item in state["canonical_files"]),
        "- Canonical tiers: " + (", ".join(tier_labels) if tier_labels else "-"),
    ]
    planning_reference = planning_reference_files(planning_state)
    if planning_reference:
        current_sprint_lines.append(f"- Planning mode: `{planning_reference[0]}`")
    for path, label in OPTIONAL_CURRENT_WORK_REFERENCES:
        if path in canonical_files:
            current_sprint_lines.append(f"- {label}: `{path}`")
    current_sprint_lines.append("- Dashboard: `docs-site/index.html`")

    lines: list[str] = [
        "# Current Work",
        "",
        "This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.",
        "Do not treat this file as the machine-readable source of truth.",
        f"Absolute times below use {DISPLAY_TIMEZONE_LABEL}.",
        "",
        f"Last updated: {format_display_timestamp(state['updated_at'])}",
        "",
        "## Objective",
        "",
        localize_embedded_timestamps(state["objective"]),
        "",
        "## Current Sprint",
        "",
        *current_sprint_lines,
        "",
    ]

    if planning_state:
        gate = planning_state.get("switch_gate", {})
        lines.extend(
            [
                "## Discussion Planning",
                "",
                f"- Session: `{planning_state.get('session_id', '-')}`",
                f"- Status: `{planning_state.get('status', '-')}`",
                f"- Baton owner: `{planning_state.get('baton_owner', '-')}`",
                f"- Current round: `{planning_state.get('current_round', 0)}`",
                f"- Consensus: `{planning_state.get('consensus_status', '-')}`",
                f"- Human gate: `{planning_state.get('human_gate_status', '-')}`",
                f"- Ready for human: `{gate.get('ready_for_human', False)}`",
                f"- Ready to materialize execution: `{gate.get('ready_to_materialize', False)}`",
                "",
            ]
        )
    lines.extend(
        [
        "## Active Slices",
        "",
        ]
    )

    for agent in state["agents"]:
        next_text = localize_embedded_timestamps(agent.get("next") or "No active assignment")
        lines.append(f"- `{agent['name']}`: {', '.join(agent['capability_lane'])}; next: {next_text}")

    lines.extend(
        [
            "",
            "## Delivery Layers",
            "",
            "### Primary Project Work",
            "",
        ]
    )
    append_layer_table(lines, primary_tasks)
    lines.extend(
        [
            "",
            "### External / Upstream Integration Work",
            "",
        ]
    )
    append_layer_table(lines, external_tasks)

    lines.extend(
        [
            "",
            "## Recently Executed Tasks",
            "",
            f"- Archive updated: {format_display_timestamp(archive_index.get('updated_at'))}",
            f"- Terminal tasks archived: `{int(archive_counts.get('total') or 0)}` total, `{int(archive_counts.get('completed') or 0)}` completed, `{int(archive_counts.get('superseded') or 0)}` superseded",
            "",
            "| ID | Phase | Task | Owner | Outcome | Archived At | Snapshot |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    if recent_terminal_tasks:
        for task in recent_terminal_tasks:
            lines.append(
                "| `{id}` | {phase} | {title} | {owner} | {outcome} | {archived_at} | `{snapshot}` |".format(
                    id=cell(task.get("task_id")),
                    phase=cell(task.get("phase")),
                    title=cell(task.get("title") or "-"),
                    owner=cell(task.get("owner")),
                    outcome=cell(task.get("terminal_outcome")),
                    archived_at=cell(format_display_timestamp(task.get("archived_at"))),
                    snapshot=cell(task.get("snapshot_path") or "-"),
                )
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - | - |")

    lines.extend(["", "## Task Board", "", "| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |", "|---|---|---|---|---|---|---|---|---|---|"])

    for task in state["tasks"]:
        depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
        lines.append(
            "| `{id}` | {phase} | {title} | {summary} | {owner} | {reviewer} | {status} | {depends} | {last_update} | {next} |".format(
                id=cell(task["id"]),
                phase=cell(task["phase"]),
                title=cell(display_task_title(task)),
                summary=cell(task.get("summary_zh") or "-"),
                owner=cell(task["owner"]),
                reviewer=cell(task["reviewer"]),
                status=cell(task["status"]),
                depends=cell(depends),
                last_update=cell(format_display_timestamp(task.get("last_update"))),
                next=cell(localize_embedded_timestamps(task.get("next") or "-")),
            )
        )

    lines.extend(["", "## Handoff Queue", "", "| Task | From | To | Message | Status | Created At |", "|---|---|---|---|---|---|"])
    pending_handoffs = [handoff for handoff in state.get("handoffs", []) if handoff.get("status") != "done"]
    if pending_handoffs:
        for handoff in pending_handoffs:
            lines.append(
                f"| `{handoff['task_id']}` | {handoff['from']} | {handoff['to']} | {cell(localize_embedded_timestamps(handoff['message']))} | {handoff['status']} | {cell(format_display_timestamp(handoff['created_at']))} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - |")

    lines.extend(["", "## Blockers", "", "| Task | Owner | Waiting For | Message | Status |", "|---|---|---|---|---|"])
    open_blockers = [blocker for blocker in state.get("blockers", []) if blocker.get("status") == "open"]
    if open_blockers:
        for blocker in open_blockers:
            lines.append(
                f"| `{blocker['task_id']}` | {blocker['owner']} | {blocker['waiting_for']} | {blocker['message']} | {blocker['status']} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - |")

    lines.extend(["", "## Review Notes", "", "| Task | Reviewer | 修正重點 | Review File |", "|---|---|---|---|"])
    review_tasks = [task for task in state["tasks"] if task.get("review_notes_zh")]
    if review_tasks:
        for task in review_tasks:
            note_html = "<br>".join(localize_embedded_timestamps(note) for note in task.get("review_notes_zh", []))
            lines.append(
                f"| `{task['id']}` | {cell(task['reviewer'])} | {cell(note_html)} | {cell(task.get('review_file') or '-')} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - |")

    coordination_counts = coordination_summary.get("counts", {}) if isinstance(coordination_summary.get("counts"), dict) else {}
    lines.extend(
        [
            "",
            "## Lovable Coordination",
            "",
            f"- Last coordination scan: {format_display_timestamp(coordination_summary.get('last_scan_at'))}",
            f"- Tracked features: `{coordination_counts.get('tracked_features', 0)}`",
            f"- Lovable-ready packets: `{coordination_counts.get('lovable_ready', 0)}`",
            f"- Waiting for Lovable/front-end: `{coordination_counts.get('waiting_for_lovable', 0)}`",
            f"- UI-done returned: `{coordination_counts.get('ui_done_received', 0)}`",
            f"- Frontend feedback returned: `{coordination_counts.get('frontend_feedback_received', 0)}`",
            f"- Open BFF gaps: `{coordination_counts.get('open_bff_gaps', 0)}`",
            f"- Backend route live: `{coordination_counts.get('backend_route_live', 0)}`",
            f"- Pantheon handoff published: `{coordination_counts.get('pantheon_handoff_published', 0)}`",
            f"- Mirrored to front default branch: `{coordination_counts.get('mirrored_to_front_default_branch', 0)}`",
            f"- Dispatch recorded in coordinator state: `{coordination_counts.get('dispatch_emitted', 0)}`",
            f"- Receiver-visible payload on front default branch: `{coordination_counts.get('front_receiver_applied', 0)}`",
            f"- Lovable consumed packet: `{coordination_counts.get('lovable_consumed', 0)}`",
            f"- UI activated: `{coordination_counts.get('ui_activated', 0)}`",
            f"- Runtime verified: `{coordination_counts.get('runtime_verified', 0)}`",
            "",
            "| Feature | Screen | Stage | Lovable Ready | Mirrored | UI Done | Feedback | Next Action |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    coordination_features = coordination_summary.get("features") if isinstance(coordination_summary.get("features"), list) else []
    if coordination_features:
        for feature in coordination_features:
            lines.append(
                "| `{feature_id}` | {screen} | `{stage}` | {lovable_ready} | {mirrored} | {ui_done} | {feedback} | {next_action} |".format(
                    feature_id=cell(feature.get("feature_id") or "-"),
                    screen=cell(feature.get("screen") or "-"),
                    stage=cell(feature.get("stage") or "-"),
                    lovable_ready="yes" if feature.get("lovable_ready") else "no",
                    mirrored="yes" if feature.get("mirrored_to_target_repo") else "no",
                    ui_done="yes" if feature.get("has_ui_done") else "no",
                    feedback="yes" if feature.get("has_frontend_feedback") else "no",
                    next_action=cell(localize_embedded_timestamps(feature.get("next_action") or "-")),
                )
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - | - | - |")

    route_live_activation_archive = archived_route_live_activation_modules()
    route_live_activation_outside_feature_rows = modules_outside_coordination_feature_rows(
        route_live_activation_archive,
        coordination_features,
    )
    if route_live_activation_outside_feature_rows:
        lines.extend(
            [
                "",
                "Tracked-feature note: the table above only lists modules that currently have coordination feature records.",
                "Archive-done route-live activation publication lanes that remain outside explicit feature rows: "
                + ", ".join(f"`{module}`" for module in route_live_activation_outside_feature_rows) + ".",
                "Do not read those omitted modules as open Pantheon backlog purely because they are absent from the coordination feature table.",
            ]
        )

    lines.extend(["", "## Latest Checkpoints", ""])
    if current_logs:
        for entry in current_logs:
            task_id = f" `{entry['task_id']}`" if entry.get("task_id") else ""
            timestamp = entry.get("ts") or entry.get("timestamp")
            lines.append(
                f"- {format_display_timestamp(timestamp)} {entry.get('agent') or 'Unknown'}:{task_id} "
                f"{localize_embedded_timestamps(activity_log_message(entry))}"
            )
    else:
        lines.append("- No checkpoints yet.")

    CURRENT_WORK_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def archived_route_live_activation_modules() -> list[str]:
    task_modules = {
        "APP-003-ROUTE-LIVE-FRONTEND-001": ["CW-02", "KW-04", "KW-05"],
        "APP-003-ROUTE-LIVE-FRONTEND-002": ["RW-02", "RW-04", "RW-05", "KW-02", "KW-03", "TW-01", "TW-02", "TW-04"],
    }
    modules: list[str] = []
    for task_id, task_module_list in task_modules.items():
        snapshot = archived_task_snapshot(task_id)
        if snapshot is None:
            continue
        task = snapshot.get("task") if isinstance(snapshot, dict) else None
        if not isinstance(task, dict):
            continue
        if str(task.get("status") or "").lower() != "done":
            continue
        for module in task_module_list:
            if module not in modules:
                modules.append(module)
    return modules


def feature_module_identifier(feature_id: Any) -> str | None:
    candidate = str(feature_id or "").strip()
    if not candidate:
        return None
    match = FEATURE_MODULE_RE.match(candidate)
    if match:
        return match.group(1)
    return None


def modules_outside_coordination_feature_rows(
    modules: list[str],
    coordination_features: list[dict[str, Any]],
) -> list[str]:
    tracked_modules: set[str] = set()
    for feature in coordination_features:
        if not isinstance(feature, dict):
            continue
        module = feature_module_identifier(feature.get("feature_id"))
        if module:
            tracked_modules.add(module)

    return [module for module in modules if module not in tracked_modules]


def normalize_worker_actor(worker: dict[str, Any]) -> str:
    for candidate in (worker.get("logical_agent_id"), worker.get("agent_id"), worker.get("target_agent"), worker.get("provider")):
        normalized = str(candidate or "").strip().lower().replace("-", "_")
        if re.match(r"^codex1_[1-4]$", normalized):
            return "Codex"
        if re.match(r"^codex2_[1-4]$", normalized):
            return "Codex2"
        canonical = canonical_agent_name(candidate)
        if canonical:
            return canonical
        lowered = str(candidate or "").strip().lower()
        if lowered in {"grok", "copilot"}:
            return "Copilot"
    return str(worker.get("agent_id") or worker.get("provider") or "").strip()


def runtime_dispatch_mode(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "execution"
    explicit = str(payload.get("dispatch_mode") or "").strip()
    if explicit:
        return explicit
    request_snapshot = payload.get("request_snapshot") if isinstance(payload.get("request_snapshot"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else request_snapshot.get("metadata", {})
    if isinstance(metadata.get("planning"), dict) and metadata.get("planning"):
        return str(metadata["planning"].get("mode") or "discussion_planning")
    if isinstance(metadata.get("chair"), dict) and metadata.get("chair"):
        return "chair_review"
    if isinstance(metadata.get("coordination"), dict) and metadata.get("coordination"):
        return "coordination"
    reason = str(payload.get("reason") or request_snapshot.get("reason") or "").strip()
    if reason.startswith("discussion_planning_"):
        return "discussion_planning"
    if reason.startswith("chair_review:"):
        return "chair_review"
    if reason.startswith("coordination:"):
        return "coordination"
    return "execution"


def expected_task_actor(task: dict[str, Any]) -> str:
    if str(task.get("status") or "").lower() == "review":
        return canonical_agent_name(task.get("reviewer"))
    return canonical_agent_name(task.get("owner"))


def pid_is_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    state = proc_pid_state(value)
    if not state:
        return False
    return state.upper() not in {"Z", "X"}


def proc_pid_state(pid: Any) -> str | None:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        stat = Path(f"/proc/{value}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return stat.rsplit(")", 1)[1].strip().split()[0]
    except IndexError:
        return None


def worker_has_live_runtime(worker: dict[str, Any], *, pid_alive: bool | None = None) -> bool:
    status = str(worker.get("status") or "").strip().lower()
    pid = worker.get("pid")
    has_pid = pid not in {None, "", 0, "0"}
    if pid_alive is None and has_pid:
        pid_alive = pid_is_alive(pid)

    if status in {"running", "started", "waiting_approval"}:
        if has_pid:
            return bool(pid_alive)
        return True
    if status in {"suspended_approval", "manual_pending", "retry_backoff", "fallback", "stalled"}:
        if has_pid:
            return bool(pid_alive)
        return False
    return False


def normalize_runtime_workers(state: dict[str, Any], orchestrator_state: dict[str, Any]) -> list[dict[str, Any]]:
    resolver = task_resolver(state)
    rows: list[dict[str, Any]] = []
    for run_id, worker in (orchestrator_state.get("workers", {}) or {}).items():
        task_id = str(worker.get("task_id") or "").strip()
        task = resolver.get(task_id) if task_id else None
        request_snapshot = worker.get("request_snapshot") if isinstance(worker.get("request_snapshot"), dict) else {}
        request_metadata = request_snapshot.get("metadata") if isinstance(request_snapshot.get("metadata"), dict) else {}
        handoff = request_metadata.get("handoff") if isinstance(request_metadata.get("handoff"), dict) else None
        task_status = str(task.get("status") or "") if task else None
        task_source = resolver.source(task_id) if task_id else None
        worker_status = str(worker.get("status") or "")
        reason = worker.get("reason") or request_snapshot.get("reason")
        if task is None and str(reason or "") == "handoff_pending" and handoff:
            task_status = str(handoff.get("status") or "pending")
            task_source = "handoff"
        pid = worker.get("pid")
        pid_state = proc_pid_state(pid) if pid not in {None, "", 0, "0"} else None
        pid_alive = bool(pid_state and pid_state.upper() not in {"Z", "X"}) if pid_state is not None else None
        live_runtime = worker_has_live_runtime(worker, pid_alive=pid_alive)
        if worker_status in {"superseded", "reassigned"}:
            bucket = "transition"
        elif task_status == "done" or worker_status in {"completed", "failed"}:
            bucket = "completed"
        elif not live_runtime and worker_status in {"running", "started"} and pid not in {None, "", 0, "0"}:
            bucket = "stale"
        elif live_runtime and worker_status in {"running", "started"}:
            bucket = "running"
        else:
            bucket = "pending"
        rows.append(
            {
                "run_id": run_id,
                "task_id": worker.get("task_id"),
                "queue_event_id": worker.get("queue_event_id"),
                "actor": normalize_worker_actor(worker),
                "provider": worker.get("provider"),
                "logical_agent_id": worker.get("logical_agent_id"),
                "dispatch_slot": worker.get("dispatch_slot"),
                "dispatch_slot_id": worker.get("dispatch_slot_id"),
                "quota_group": worker.get("quota_group"),
                "status": worker_status,
                "bucket": bucket,
                "task_status": task_status,
                "task_source": task_source,
                "reason": reason,
                "handoff": handoff,
                "delivery_mode": worker.get("mode"),
                "dispatch_mode": runtime_dispatch_mode(worker),
                "last_event_at": worker.get("last_event_at"),
                "started_at": worker.get("started_at"),
                "last_error": worker.get("last_error"),
                "pid": pid,
                "pid_alive": pid_alive,
                "pid_state": pid_state,
                "is_live_runtime": live_runtime,
            }
        )
    rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
    return rows


def normalize_runtime_queue(orchestrator_state: dict[str, Any]) -> list[dict[str, Any]]:
    queue_records = ((orchestrator_state.get("queue") or {}).get("events") or {})
    workers_by_event: dict[str, dict[str, Any]] = {}
    for run_id, worker in (orchestrator_state.get("workers", {}) or {}).items():
        queue_event_id = worker.get("queue_event_id")
        if queue_event_id:
            workers_by_event[str(queue_event_id)] = {"run_id": run_id, **worker}
    rows: list[dict[str, Any]] = []
    for event_id, event in queue_records.items():
        linked_worker = workers_by_event.get(str(event_id), {})
        rows.append(
            {
                "id": event_id,
                "task_id": event.get("task_id") or linked_worker.get("task_id"),
                "status": event.get("status"),
                "agent": canonical_agent_name(event.get("target_display_name") or event.get("target_agent") or linked_worker.get("agent_id")),
                "provider": event.get("provider") or linked_worker.get("provider"),
                "reason": event.get("reason") or linked_worker.get("reason") or (linked_worker.get("request_snapshot") or {}).get("reason"),
                "dispatch_mode": runtime_dispatch_mode(event or linked_worker),
                "run_id": event.get("run_id") or linked_worker.get("run_id"),
                "last_event_at": event.get("last_event_at") or event.get("processed_at") or event.get("last_attempt_at") or linked_worker.get("last_event_at"),
            }
        )
    rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
    return rows


def mismatch_resolution_hint(item: dict[str, Any]) -> str:
    mismatch_type = str(item.get("type") or "")
    if mismatch_type == "worker_without_task":
        return "先檢查 dispatch/request snapshot 是否漏掉 task_id；如果是舊 worker，應重派成帶 task_id 的新 run。"
    if mismatch_type == "worker_task_missing":
        return "先確認 task 是否被移除或改名；若 task 已失效，應停掉 worker，否則重建對應 task。"
    if mismatch_type == "worker_assignment_mismatch":
        return "先對齊 owner/reviewer 與 runtime actor；若已改派，先把 task board assignment 寫回，再重新 dispatch。"
    if mismatch_type == "running_worker_on_todo":
        return "先把 task 狀態推成 in_progress；若 worker 是誤派，則回退 queue 或直接停掉該 run。"
    if mismatch_type == "running_worker_on_done":
        return "先確認這是不是殘留 worker；若 task 已確定 done，應停掉 worker 並清理 queue record。"
    if mismatch_type == "active_task_without_worker":
        return "要嘛重新 dispatch expected actor，要嘛把 task 狀態降回 todo/blocking truth，避免假 active。"
    if mismatch_type == "queue_started_without_worker":
        return "先檢查 queue record 是否卡在 started；如果 worker 已消失，重設 queue 或重新 dispatch。"
    if mismatch_type == "approval_missing_task":
        return "先清掉 stale approval，或先恢復 task board 中的 task，再進行批准。"
    return "先對齊 task board、queue、runtime 三者的真相，再決定是重派、回退，還是清理殘留記錄。"


def detect_truth_mismatches(
    state: dict[str, Any],
    workers: list[dict[str, Any]],
    queue_events: list[dict[str, Any]],
    approval_state: dict[str, Any],
    resolver: TaskResolver,
    orchestrator_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    task_map = {task["id"]: task for task in state.get("tasks", [])}
    orchestrator = orchestrator_state or {}
    provider_guardrails = orchestrator.get("provider_guardrails") if isinstance(orchestrator.get("provider_guardrails"), dict) else {}
    (
        provider_guardrails.get("dispatch_pauses")
        if isinstance(provider_guardrails.get("dispatch_pauses"), dict)
        else orchestrator.get("dispatch_pauses")
        if isinstance(orchestrator.get("dispatch_pauses"), dict)
        else {}
    )
    live_workers = [
        worker
        for worker in workers
        if worker.get("bucket") in {"running", "pending"} and worker.get("is_live_runtime")
    ]
    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    mismatches: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending_approval_run_ids = {
        str(approval.get("worker_run_id") or "").strip()
        for approval in (approval_state.get("pending") or [])
        if str(approval.get("worker_run_id") or "").strip()
    }
    pending_approval_task_ids = {
        str(approval.get("task_id") or "").strip()
        for approval in (approval_state.get("pending") or [])
        if str(approval.get("task_id") or "").strip()
    }
    approval_worker_modes = {
        str(worker.get("run_id") or "").strip(): str(worker.get("dispatch_mode") or "").strip()
        for worker in workers
        if str(worker.get("run_id") or "").strip()
    }

    def related_live_worker_covers_task(task: dict[str, Any]) -> bool:
        expected_actor = expected_task_actor(task)
        if not expected_actor:
            return False
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return False

        parent_id = str(task.get("helper_parent") or "").strip()
        if parent_id:
            for worker in live_workers_by_task.get(parent_id, []):
                if canonical_agent_name(worker.get("actor") or worker.get("agent_id")) == expected_actor:
                    return True

        for related_id, related_task in task_map.items():
            if str(related_task.get("helper_parent") or "").strip() != task_id:
                continue
            for worker in live_workers_by_task.get(related_id, []):
                if canonical_agent_name(worker.get("actor") or worker.get("agent_id")) == expected_actor:
                    return True

        return False

    def push(payload: dict[str, Any]) -> None:
        key = str(payload.get("id") or f"{payload.get('type')}:{payload.get('task_id')}:{payload.get('worker_run_id')}:{payload.get('queue_event_id')}")
        if key in seen:
            return
        payload.setdefault("resolution_hint", mismatch_resolution_hint(payload))
        seen.add(key)
        mismatches.append(payload)

    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)
        else:
            if str(worker.get("dispatch_mode") or "").strip() == "chair_review":
                continue
            push(
                {
                    "id": f"worker-without-task:{worker.get('run_id')}",
                    "type": "worker_without_task",
                    "severity": "medium",
                    "title": "Live worker 沒有綁到 task",
                    "summary": f"{worker.get('actor') or '-'} 的 worker 已在跑，但沒有 task_id。",
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )
            continue

        task = task_map.get(task_id)
        if task is None:
            if str(worker.get("dispatch_mode") or "").strip() in {"discussion_planning", "coordination", "chair_review"}:
                continue
            if resolver.source(task_id) == "archive":
                continue
            if worker.get("task_source") == "handoff":
                continue
            push(
                {
                    "id": f"worker-task-missing:{worker.get('run_id')}",
                    "type": "worker_task_missing",
                    "severity": "high",
                    "title": "Live worker 指向不存在的 task",
                    "summary": f"{worker.get('actor') or '-'} 的 worker 綁到 {task_id}，但 task board 找不到這個 task。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )
            continue

        task_status = str(task.get("status") or "").lower()
        expected_actor = expected_task_actor(task)
        actual_actor = canonical_agent_name(worker.get("actor") or worker.get("agent_id"))
        if expected_actor and actual_actor and expected_actor != actual_actor:
            push(
                {
                    "id": f"worker-assignment:{worker.get('run_id')}",
                    "type": "worker_assignment_mismatch",
                    "severity": "medium" if task_status == "review" else "high",
                    "title": "Live worker 與 task 指派對不上",
                    "summary": f"{task_id} 目前應由 {expected_actor} 接手，但 live worker 來自 {actual_actor}。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "expected_actor": expected_actor,
                    "actual_actor": actual_actor,
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

        if worker.get("bucket") == "running" and task_status == "todo":
            push(
                {
                    "id": f"running-worker-on-todo:{worker.get('run_id')}",
                    "type": "running_worker_on_todo",
                    "severity": "medium",
                    "title": "Worker 已在跑，但 task 還是 todo",
                    "summary": f"{task_id} 有 live running worker，但 task status 仍是 todo。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

        if worker.get("bucket") == "running" and task_status == "done":
            push(
                {
                    "id": f"running-worker-on-done:{worker.get('run_id')}",
                    "type": "running_worker_on_done",
                    "severity": "high",
                    "title": "Task 已完成，但 worker 仍在跑",
                    "summary": f"{task_id} 已是 done，但還有 live running worker。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

    for task in state.get("tasks", []):
        task_status = str(task.get("status") or "").lower()
        if task_status != "in_progress":
            continue
        expected_actor = expected_task_actor(task)
        if str(task.get("id") or "").strip() in pending_approval_task_ids:
            continue
        if live_workers_by_task.get(task["id"]):
            continue
        if related_live_worker_covers_task(task):
            continue
        push(
            {
                "id": f"active-task-without-worker:{task['id']}",
                "type": "active_task_without_worker",
                "severity": "medium",
                "title": "Active task 沒有 live worker",
                "summary": f"{task['id']} 在 task board 上是 {task_status}，但目前沒有對應的 live worker。",
                "task_id": task["id"],
                "expected_actor": expected_actor,
                "detected_at": task.get("last_update"),
            }
        )

    live_queue_ids = {str(worker.get("queue_event_id") or "") for worker in live_workers if worker.get("queue_event_id")}
    for event in queue_events:
        event_status = str(event.get("status") or "").lower()
        if event_status not in {"started", "manual_pending"}:
            continue
        if (
            str(event.get("run_id") or "").strip() in pending_approval_run_ids
            or str(event.get("task_id") or "").strip() in pending_approval_task_ids
        ):
            continue
        if str(event.get("id") or "") in live_queue_ids:
            continue
        push(
            {
                "id": f"queue-without-worker:{event.get('id')}",
                "type": "queue_started_without_worker",
                "severity": "medium",
                "title": "Queue record 已啟動，但找不到 live worker",
                "summary": f"{event.get('task_id') or event.get('id')} 的 queue record 已是 {event_status}，但 runtime 沒有對應 worker。",
                "task_id": event.get("task_id"),
                "queue_event_id": event.get("id"),
                "detected_at": event.get("last_event_at"),
            }
        )

    for approval in (approval_state.get("pending") or []):
        task_id = str(approval.get("task_id") or "").strip()
        worker_run_id = str(approval.get("worker_run_id") or "").strip()
        if approval_worker_modes.get(worker_run_id) in {"discussion_planning", "coordination"}:
            continue
        if not task_id or task_id in task_map or resolver.source(task_id) == "archive":
            continue
        push(
            {
                "id": f"approval-missing-task:{approval.get('id') or approval.get('approval_id') or task_id}",
                "type": "approval_missing_task",
                "severity": "medium",
                "title": "Approval queue 指向不存在的 task",
                "summary": f"待批准項目 {task_id} 已不在 task board 中。",
                "task_id": task_id,
                "detected_at": approval.get("created_at"),
            }
        )

    severity_order = {"high": 0, "medium": 1, "low": 2}
    mismatches.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity") or "medium"), 9),
            str(item.get("detected_at") or ""),
        )
    )
    return live_workers, mismatches


def normalized_source_ref(task: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    payload = task.get("source_ref")
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        normalized[str(key)] = text
    return normalized


def build_bridge_summary(state: dict[str, Any], planning: dict[str, Any]) -> dict[str, Any]:
    resolver = task_resolver(state)
    resolver.active_task_map()
    proposed = planning.get("proposed_execution_tasks") or []
    contract = planning.get("materialization_contract") if isinstance(planning.get("materialization_contract"), dict) else {}
    artifacts = planning.get("artifacts") if isinstance(planning.get("artifacts"), dict) else {}
    consensus_packet = str(contract.get("consensus_packet") or ((artifacts.get("consensus_packet") or {}).get("path")) or "").strip()
    execution_materialization = str(
        contract.get("execution_materialization")
        or ((artifacts.get("execution_materialization") or {}).get("path"))
        or ""
    ).strip()
    session_id = str(contract.get("session_id") or planning.get("session_id") or "").strip()
    planning_backed_tasks = [
        task
        for task in state.get("tasks", [])
        if str(task.get("source_plane") or "").strip().lower() == "planning"
    ]

    status_counts = {
        "done": 0,
        "review_approved": 0,
        "in_progress": 0,
        "review": 0,
        "todo": 0,
        "blocked": 0,
    }
    pending_proposals: list[dict[str, Any]] = []
    active_materialized: list[dict[str, Any]] = []
    materialized_task_ids: list[str] = []
    missing_source_ref_count = 0
    current_session_materialized = 0

    for proposal in proposed:
        task_id = str(proposal.get("id") or "").strip()
        if not task_id:
            continue
        current = resolver.get(task_id)
        if current is None:
            pending_proposals.append(
                {
                    "id": task_id,
                    "title": str(proposal.get("title") or "").strip(),
                    "summary_zh": str(proposal.get("summary_zh") or "").strip(),
                    "owner": str(proposal.get("owner") or "").strip(),
                    "reviewer": str(proposal.get("reviewer") or "").strip(),
                }
            )
            continue

        materialized_task_ids.append(task_id)
        current_status = str(current.get("status") or "").lower()
        if current_status in status_counts:
            status_counts[current_status] += 1
        source_ref = normalized_source_ref(current)
        if not source_ref:
            missing_source_ref_count += 1
        elif session_id and str(source_ref.get("session_id") or "").strip() == session_id:
            current_session_materialized += 1
        if current_status != "done":
            active_materialized.append(
                {
                    "id": task_id,
                    "title": str(current.get("title") or proposal.get("title") or "").strip(),
                    "summary_zh": str(current.get("summary_zh") or proposal.get("summary_zh") or "").strip(),
                    "status": str(current.get("status") or "").strip(),
                    "owner": str(current.get("owner") or "").strip(),
                    "reviewer": str(current.get("reviewer") or "").strip(),
                }
            )

    return {
        "source_plane": "planning",
        "session_id": session_id,
        "phase": str(contract.get("phase") or planning.get("phase") or "").strip(),
        "profile": str(contract.get("profile") or planning.get("profile") or "").strip(),
        "planning_dir": str(contract.get("planning_dir") or planning.get("planning_dir") or "").strip(),
        "session_file": str(contract.get("session_file") or planning.get("session_file") or "").strip(),
        "consensus_packet": consensus_packet,
        "execution_materialization": execution_materialization,
        "proposed_total": len(proposed),
        "materialized_count": len(materialized_task_ids),
        "pending_materialization_count": len(pending_proposals),
        "done": status_counts["done"],
        "review_approved": status_counts["review_approved"],
        "in_progress": status_counts["in_progress"],
        "review": status_counts["review"],
        "todo": status_counts["todo"],
        "blocked": status_counts["blocked"],
        "materialized_task_ids": materialized_task_ids,
        "pending_proposals": pending_proposals,
        "active_materialized_tasks": active_materialized,
        "planning_backed_total": len(planning_backed_tasks),
        "planning_backed_active": sum(
            1 for task in planning_backed_tasks if str(task.get("status") or "").lower() in {"todo", "in_progress", "review", "review_approved", "blocked"}
        ),
        "current_session_materialized": current_session_materialized,
        "missing_source_ref_count": missing_source_ref_count,
    }


def coordination_payload_resolved(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    if payload.get("resolved_at"):
        return True
    status = str(payload.get("status") or "").strip().lower()
    return status in {"resolved", "completed", "done"}


def normalize_coordination_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def coordination_payload_status(entry: dict[str, Any] | None) -> str:
    if not isinstance(entry, dict):
        return ""
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    return normalize_coordination_token(payload.get("status"))


def coordination_payload_field(entry: dict[str, Any] | None, field: str) -> Any:
    if not isinstance(entry, dict):
        return None
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    return payload.get(field)


def load_local_coordination_payload(path_value: str) -> dict[str, Any] | None:
    candidate = str(path_value or "").strip()
    if not candidate or candidate.startswith("../") or "://" in candidate:
        return None
    local_path = ROOT / candidate
    if not local_path.exists() or not local_path.is_file():
        return None
    try:
        text = local_path.read_text(encoding="utf-8")
        if local_path.suffix == ".json":
            payload = json.loads(text)
        else:
            if yaml is None:
                return None
            payload = yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, *YAML_ERROR_TYPES):
        return None
    return payload if isinstance(payload, dict) else None


def coordination_repo_root(repo_id: str) -> Path | None:
    config = load_config()
    root = repository_local_path(config, repo_id)
    if isinstance(root, Path):
        return root
    if repo_id == "front_ai_trading_system":
        fallback = ROOT.parent / "front-ai-trading-system"
        return fallback if fallback.exists() else None
    if repo_id == "pantheon":
        return ROOT
    return None


def coordination_audit_matches(repo_root: Path | None, feature_id: str, marker: str) -> bool:
    if repo_root is None:
        return False
    audit_dir = repo_root / ".coordination" / "audit"
    if not audit_dir.exists():
        return False
    return any(audit_dir.glob(f"{feature_id}-{marker}-*.json"))


def coordination_repo_payload_exists(repo_root: Path | None, rel_path: str | None) -> bool:
    candidate = str(rel_path or "").strip()
    if repo_root is None or not candidate:
        return False
    if candidate.startswith("/") or ".." in candidate:
        return False
    return (repo_root / candidate).is_file()


def coordination_payload_has_runtime_verification(entry: dict[str, Any] | None) -> bool:
    payload = entry.get("payload") if isinstance(entry, dict) else None
    if not isinstance(payload, dict):
        return False
    if payload.get("runtime_verified_at") or payload.get("verified_runtime_ref"):
        return True
    payload_type = normalize_coordination_token(payload.get("type"))
    return payload_type in {"needs_runtime", "bff_gap"} and bool(payload.get("resolved_at"))


def coordination_state_flags(feature: dict[str, Any]) -> dict[str, bool]:
    feature_id = str(feature.get("feature_id") or "").strip()
    contract_ready = coordination_payload_entry(feature, "responses", "contract-ready")
    lovable_task = coordination_payload_entry(feature, "responses", "lovable-ui-task")
    backend_delivery = coordination_payload_entry(feature, "responses", "backend-delivery")
    ui_done = coordination_payload_entry(feature, "requests", "ui-done")
    frontend_feedback = coordination_payload_entry(feature, "requests", "frontend-feedback")
    frontend_feedback_response = coordination_payload_entry(feature, "responses", "frontend-feedback")
    bff_gap = coordination_payload_entry(feature, "requests", "bff-gap")
    needs_runtime = coordination_payload_entry(feature, "requests", "needs-runtime")

    front_root = coordination_repo_root("front_ai_trading_system")
    pantheon_root = coordination_repo_root("pantheon")
    mirrored_contract_path = f".coordination/responses/{feature_id}-contract-ready.yaml" if feature_id else None
    mirrored_delivery_path = f".coordination/responses/{feature_id}-backend-delivery.yaml" if feature_id else None

    mirrored_to_front = bool(feature.get("mirrored_to_target_repo")) or coordination_repo_payload_exists(front_root, mirrored_contract_path)
    if not mirrored_to_front:
        mirrored_to_front = coordination_repo_payload_exists(front_root, mirrored_delivery_path)

    dispatch_recorded = bool(feature.get("last_dispatched_at")) or coordination_audit_matches(
        pantheon_root, feature_id, "dispatch-emitted"
    )
    receiver_visible = mirrored_to_front and (
        bool(feature.get("last_dispatched_at"))
        or coordination_audit_matches(front_root, feature_id, "received")
    )

    lovable_consumed = any(bool(entry) for entry in (ui_done, frontend_feedback, frontend_feedback_response, bff_gap, needs_runtime))
    ui_activated = any(bool(entry) for entry in (ui_done, frontend_feedback, frontend_feedback_response))
    runtime_verified = any(
        coordination_payload_has_runtime_verification(entry)
        for entry in (needs_runtime, bff_gap, ui_done, frontend_feedback, frontend_feedback_response, backend_delivery)
    )

    return {
        "backend_route_live": bool(contract_ready or backend_delivery),
        "pantheon_handoff_published": bool(contract_ready or lovable_task or backend_delivery),
        "mirrored_to_front_default_branch": mirrored_to_front,
        "dispatch_emitted": dispatch_recorded,
        "front_receiver_applied": receiver_visible,
        "lovable_consumed": lovable_consumed,
        "ui_activated": ui_activated,
        "runtime_verified": runtime_verified,
    }


def coordination_local_response_path(feature: dict[str, Any], payload_type: str) -> str | None:
    feature_id = str(feature.get("feature_id") or "").strip()
    if not feature_id:
        return None
    candidate = ROOT / ".coordination" / "responses" / f"{feature_id}-{payload_type}.yaml"
    if candidate.exists():
        return str(candidate.relative_to(ROOT))
    return None


def coordination_payload_entry(feature: dict[str, Any], bucket: str, payload_type: str) -> dict[str, Any] | None:
    typed_key = f"{bucket}_by_type"
    typed_entries = feature.get(typed_key)
    if isinstance(typed_entries, dict):
        candidate = typed_entries.get(payload_type)
        if isinstance(candidate, dict):
            if bucket == "responses" and payload_type == "frontend-feedback":
                local_payload = load_local_coordination_payload(str(candidate.get("path") or ""))
                if isinstance(local_payload, dict):
                    overlaid = dict(candidate)
                    overlaid["payload"] = local_payload
                    return overlaid
            return candidate

    latest_key = "latest_request" if bucket == "requests" else "latest_response"
    latest_path_key = f"{latest_key}_path"
    latest_payload = feature.get(latest_key)
    if isinstance(latest_payload, dict) and str(latest_payload.get("type") or "").strip() == payload_type:
        return {
            "type": payload_type,
            "path": feature.get(latest_path_key) or feature.get("latest_path"),
            "payload": latest_payload,
            "updated_at": latest_payload.get("updated_at") or latest_payload.get("created_at") or feature.get("last_updated_at"),
            "source_repo_id": feature.get("source_repo_id"),
            "target_repo_id": feature.get("target_repo_id"),
        }

    if bucket == "responses" and payload_type == "frontend-feedback":
        local_path = coordination_local_response_path(feature, payload_type)
        if local_path:
            local_payload = load_local_coordination_payload(local_path)
            if isinstance(local_payload, dict):
                return {
                    "type": payload_type,
                    "path": local_path,
                    "payload": local_payload,
                    "updated_at": local_payload.get("reviewed_at") or feature.get("last_updated_at"),
                    "source_repo_id": feature.get("source_repo_id"),
                    "target_repo_id": feature.get("target_repo_id"),
                }

    if bucket == "responses" and payload_type == "lovable-ui-task" and isinstance(feature.get("lovable_task"), dict):
        lovable_payload = feature["lovable_task"]
        return {
            "type": payload_type,
            "path": feature.get("lovable_task_path"),
            "payload": lovable_payload,
            "updated_at": lovable_payload.get("updated_at") or lovable_payload.get("created_at") or feature.get("last_updated_at"),
            "source_repo_id": feature.get("source_repo_id"),
            "target_repo_id": feature.get("target_repo_id"),
        }

    return None


def coordination_review_snapshot(feature_id: str | None) -> dict[str, str] | None:
    candidate = str(feature_id or "").strip()
    if not candidate:
        return None
    review_path = ROOT / ".coordination" / "reviews" / f"{candidate}-review.md"
    if not review_path.exists():
        return None
    try:
        text = review_path.read_text(encoding="utf-8")
    except OSError:
        return {"path": str(review_path.relative_to(ROOT)), "disposition": "reviewed"}

    lowered = text.lower()
    disposition = "reviewed"
    decision_sections = re.findall(
        r"(?ims)^##\s+(?:final\s+decision|decision)\s*\n+(.*?)(?=^##\s|\Z)",
        text,
    )
    scoped_text = decision_sections[-1].lower() if decision_sections else lowered
    if (
        "follow-up required" in scoped_text
        or "not loop-complete" in scoped_text
        or "required follow-up" in scoped_text
        or "changes requested" in scoped_text
        or "blocked" in scoped_text
    ):
        disposition = "follow_up_required"
    elif "task scope" in scoped_text or "acceptance gate" in scoped_text:
        disposition = "reviewed"
    elif (
        "approved" in scoped_text
        or "loop is complete" in scoped_text
        or "loop-complete" in scoped_text
        or "loop complete" in scoped_text
        or "loop can close" in scoped_text
    ):
        disposition = "approved"
    return {
        "path": str(review_path.relative_to(ROOT)),
        "disposition": disposition,
    }


def coordination_stage(feature: dict[str, Any]) -> tuple[str, str]:
    frontend_feedback = coordination_payload_entry(feature, "requests", "frontend-feedback")
    frontend_feedback_response = coordination_payload_entry(feature, "responses", "frontend-feedback")
    ui_done = coordination_payload_entry(feature, "requests", "ui-done")
    bff_gap = coordination_payload_entry(feature, "requests", "bff-gap")
    contract_ready = coordination_payload_entry(feature, "responses", "contract-ready")
    lovable_task = coordination_payload_entry(feature, "responses", "lovable-ui-task")
    backend_delivery = coordination_payload_entry(feature, "responses", "backend-delivery")
    review = coordination_review_snapshot(feature.get("feature_id"))

    response_disposition = normalize_coordination_token(coordination_payload_field(frontend_feedback_response, "disposition"))
    response_can_close = bool(coordination_payload_field(frontend_feedback_response, "can_close"))
    response_lovable_status = normalize_coordination_token(coordination_payload_field(frontend_feedback_response, "lovable_ui_task_status"))
    response_coordination_stage = normalize_coordination_token(coordination_payload_field(frontend_feedback_response, "coordination_stage"))
    response_next_action = str(coordination_payload_field(frontend_feedback_response, "next_action") or "").strip()
    ui_status = coordination_payload_status(ui_done)
    ui_disposition = normalize_coordination_token(coordination_payload_field(ui_done, "pantheon_disposition"))
    lovable_status = coordination_payload_status(lovable_task)
    backend_status = coordination_payload_status(backend_delivery)

    response_marks_complete = response_can_close or response_disposition in {"approved", "close", "loop_complete"}
    response_marks_followup = response_disposition in {"blocked", "follow_up", "follow_up_required"} or response_coordination_stage in {"blocked", "follow_up", "follow_up_required"}
    explicit_loop_complete = (
        response_marks_complete
        or ui_disposition == "loop_complete"
        or lovable_status == "loop_complete"
        or backend_status == "loop_complete"
        or response_lovable_status == "loop_complete"
    )
    explicit_closed = (
        ui_status == "closed"
        or lovable_status == "closed"
        or response_lovable_status == "closed"
    )

    if explicit_loop_complete:
        return "loop_complete", "Pantheon closeout record marks the current packet loop complete."

    if response_marks_followup:
        if explicit_closed and not response_coordination_stage and response_next_action and response_next_action.lower() != "none":
            # Keep the loop visible as follow-up when the response explicitly names a next action.
            pass
        elif explicit_closed and not response_coordination_stage:
            return "closed", "Current packet record is closed for this scope; reopen only if a later follow-up cycle is dispatched."
        return "frontend_feedback_reviewed_followup", (
            f"Pantheon review is complete; follow-up remains ({response_next_action})."
            if response_next_action and response_next_action.lower() != "none"
            else "Pantheon review is complete; follow-up remains per the closeout response."
        )

    if explicit_closed:
        return "closed", "Current packet record is closed for this scope; reopen only if a later follow-up cycle is dispatched."

    if frontend_feedback:
        if review:
            if review.get("disposition") == "approved":
                return "frontend_feedback_reviewed", "Pantheon review packet approves loop closeout; finalize the closure record."
            if review.get("disposition") == "follow_up_required":
                return "frontend_feedback_reviewed_followup", "Pantheon review is complete; follow-up remains per the review packet."
            return "frontend_feedback_reviewed", "Pantheon review packet exists; inspect the recorded disposition."
        return "frontend_feedback_received", "Pantheon should review the frontend feedback bundle and decide follow-up work."
    if ui_done:
        if review:
            if review.get("disposition") == "approved":
                return "ui_done_reviewed", "Pantheon reviewed the ui-done handoff; finalize the next closure or publish step."
            if review.get("disposition") == "follow_up_required":
                return "ui_done_reviewed_followup", "Pantheon reviewed the ui-done handoff; follow-up remains per the review packet."
            return "ui_done_reviewed", "Pantheon review packet exists for the ui-done handoff; inspect the recorded disposition."
        return "ui_done_received", "Pantheon should pick up review and integration from the returned ui-done handoff."
    if bff_gap and not coordination_payload_resolved(bff_gap):
        return "bff_gap_open", "Pantheon must resolve the open BFF gap before the front-end lane can continue."
    if lovable_task or feature.get("lovable_task_path"):
        return "waiting_for_lovable", "Lovable or the front-end lane can implement the screen and emit ui-done when finished."
    if contract_ready:
        return "contract_ready", "Supervisor should publish or mirror the Lovable task packet from the contract-ready bundle."
    current_type = str(feature.get("current_payload_type") or feature.get("status") or "unknown").strip().lower().replace("-", "_")
    return current_type or "unknown", str(feature.get("next_step") or feature.get("summary") or "Awaiting next coordination payload.")


def build_coordination_summary(orchestrator_state: dict[str, Any] | None) -> dict[str, Any]:
    orchestrator = orchestrator_state or {}
    coordination = orchestrator.get("coordination") if isinstance(orchestrator.get("coordination"), dict) else {}
    raw_features = coordination.get("features") if isinstance(coordination.get("features"), dict) else {}

    features: list[dict[str, Any]] = []
    counts = {
        "tracked_features": 0,
        "lovable_ready": 0,
        "mirrored_to_target_repo": 0,
        "waiting_for_lovable": 0,
        "ui_done_received": 0,
        "frontend_feedback_received": 0,
        "open_bff_gaps": 0,
        "backend_route_live": 0,
        "pantheon_handoff_published": 0,
        "mirrored_to_front_default_branch": 0,
        "dispatch_emitted": 0,
        "front_receiver_applied": 0,
        "lovable_consumed": 0,
        "ui_activated": 0,
        "runtime_verified": 0,
    }

    for feature_id in sorted(raw_features):
        feature = raw_features.get(feature_id)
        if not isinstance(feature, dict):
            continue

        contract_ready = coordination_payload_entry(feature, "responses", "contract-ready")
        lovable_task = coordination_payload_entry(feature, "responses", "lovable-ui-task")
        ui_done = coordination_payload_entry(feature, "requests", "ui-done")
        frontend_feedback_request = coordination_payload_entry(feature, "requests", "frontend-feedback")
        frontend_feedback_response = coordination_payload_entry(feature, "responses", "frontend-feedback")
        frontend_feedback = frontend_feedback_request or frontend_feedback_response
        bff_gap = coordination_payload_entry(feature, "requests", "bff-gap")
        review = coordination_review_snapshot(feature_id)
        stage, next_action = coordination_stage(feature)
        state_flags = coordination_state_flags(feature)
        mirrored = bool(state_flags.get("mirrored_to_front_default_branch"))
        lovable_ready = bool(lovable_task or feature.get("lovable_task_path"))
        open_bff_gap = bool(stage == "bff_gap_open" and bff_gap and not coordination_payload_resolved(bff_gap))

        feature_summary = {
            "feature_id": feature_id,
            "screen": feature.get("screen"),
            "summary": feature.get("summary"),
            "source_repo": feature.get("source_repo"),
            "source_repo_id": feature.get("source_repo_id"),
            "target_repo_id": feature.get("target_repo_id"),
            "target_agent": feature.get("target_agent"),
            "worker_kind": feature.get("worker_kind"),
            "current_payload_type": feature.get("current_payload_type"),
            "stage": stage,
            "next_action": next_action,
            "last_updated_at": feature.get("last_updated_at"),
            "last_dispatched_at": feature.get("last_dispatched_at"),
            "lovable_ready": lovable_ready,
            "mirrored_to_target_repo": mirrored,
            "has_contract_ready": bool(contract_ready),
            "has_lovable_task": bool(lovable_task or feature.get("lovable_task_path")),
            "has_ui_done": bool(ui_done),
            "has_frontend_feedback": bool(frontend_feedback),
            "has_bff_gap": bool(bff_gap),
            "bff_gap_open": open_bff_gap,
            "state_flags": state_flags,
            "review_path": review.get("path") if isinstance(review, dict) else None,
            "review_disposition": review.get("disposition") if isinstance(review, dict) else None,
            "paths": {
                "contract_ready": contract_ready.get("path") if isinstance(contract_ready, dict) else None,
                "lovable_task": lovable_task.get("path") if isinstance(lovable_task, dict) else feature.get("lovable_task_path"),
                "lovable_prompt": feature.get("lovable_prompt_path"),
                "ui_done": ui_done.get("path") if isinstance(ui_done, dict) else None,
                "frontend_feedback": frontend_feedback.get("path") if isinstance(frontend_feedback, dict) else None,
                "bff_gap": bff_gap.get("path") if isinstance(bff_gap, dict) else None,
                "review": review.get("path") if isinstance(review, dict) else None,
            },
        }
        features.append(feature_summary)

        counts["tracked_features"] += 1
        counts["lovable_ready"] += int(lovable_ready)
        counts["mirrored_to_target_repo"] += int(mirrored)
        counts["waiting_for_lovable"] += int(stage == "waiting_for_lovable")
        counts["ui_done_received"] += int(bool(ui_done))
        counts["frontend_feedback_received"] += int(bool(frontend_feedback))
        counts["open_bff_gaps"] += int(open_bff_gap)
        for flag_name, enabled in state_flags.items():
            counts[flag_name] += int(bool(enabled))

    return {
        "last_scan_at": coordination.get("last_scan_at"),
        "counts": counts,
        "features": features,
    }


def build_dashboard_bundle(
    state: dict[str, Any],
    planning_state: dict[str, Any] | None,
    orchestrator_state: dict[str, Any] | None,
    approval_state: dict[str, Any] | None,
) -> dict[str, Any]:
    planning = planning_state or {}
    orchestrator = orchestrator_state or {}
    approvals = approval_state or {}
    config = load_config()
    dispatch_policy = build_dispatch_policy_summary(config)
    resolver = task_resolver(state)
    task_map = resolver.active_task_map()
    archive_index = load_archive_index()
    archive_counts = archive_index.get("counts", {}) if isinstance(archive_index.get("counts"), dict) else {}
    recent_terminal_tasks = orchestrator.get("recent_terminal_tasks")
    if not isinstance(recent_terminal_tasks, list):
        recent_terminal_tasks = recent_terminal_summaries(limit=task_archive_recent_limit())
    workers = normalize_runtime_workers(state, orchestrator)
    queue_events = [
        event
        for event in normalize_runtime_queue(orchestrator)
        if str(event.get("status") or "").lower() not in {"completed", "failed"}
        and resolver.dependency_status(str(event.get("task_id") or "")) not in {"done", TASK_TERMINAL_SUPERSEDED}
    ]
    live_workers, mismatches = detect_truth_mismatches(
        state,
        workers,
        queue_events,
        approvals,
        resolver,
        orchestrator,
    )
    bridge_summary = build_bridge_summary(state, planning)
    coordination_summary = build_coordination_summary(orchestrator)
    supervisor_state = orchestrator.get("supervisor") if isinstance(orchestrator.get("supervisor"), dict) else {}

    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)

    dispatch_pauses = (
        orchestrator.get("provider_guardrails", {}).get("dispatch_pauses")
        if isinstance(orchestrator.get("provider_guardrails"), dict)
        and isinstance(orchestrator.get("provider_guardrails", {}).get("dispatch_pauses"), dict)
        else orchestrator.get("dispatch_pauses")
        if isinstance(orchestrator.get("dispatch_pauses"), dict)
        else {}
    )
    paused_actors = {str(actor or "").strip().lower() for actor in dispatch_pauses.keys() if str(actor or "").strip()}
    actor_loads: dict[str, int] = {}
    for worker in live_workers:
        if str(worker.get("bucket") or "").lower() not in {"running", "pending"}:
            continue
        actor = canonical_agent_name(worker.get("actor") or worker.get("provider"))
        if not actor:
            continue
        actor_key = actor.lower()
        actor_loads[actor_key] = actor_loads.get(actor_key, 0) + 1

    ready_now = 0
    dependency_ready = 0
    in_progress = 0
    in_review = 0
    blocked = 0
    review_approved = 0
    done = int(archive_counts.get("completed") or 0)
    superseded = int(archive_counts.get(TASK_TERMINAL_SUPERSEDED) or 0)
    for task in state.get("tasks", []):
        status = str(task.get("status") or "").lower()
        if status == "todo" and all(dependency_is_satisfied(resolver, dep_id) for dep_id in task.get("depends_on", [])):
            dependency_ready += 1
            owner = canonical_agent_name(task.get("owner"))
            owner_key = owner.lower()
            if not owner_key:
                continue
            if owner_key in paused_actors:
                continue
            if any(worker.get("bucket") in {"running", "pending"} for worker in live_workers_by_task.get(str(task.get("id") or ""), [])):
                continue
            if actor_loads.get(owner_key, 0) >= dashboard_agent_capacity(config, owner):
                continue
            ready_now += 1
        elif status == "in_progress":
            in_progress += 1
        elif status == "review":
            in_review += 1
        elif status == "blocked":
            blocked += 1
        elif status == "review_approved":
            review_approved += 1

    worker_task_links: list[dict[str, Any]] = []
    mismatch_index: dict[tuple[str, str], list[str]] = {}
    mismatch_detail_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for mismatch in mismatches:
        task_id = str(mismatch.get("task_id") or "")
        run_id = str(mismatch.get("worker_run_id") or "")
        mismatch_index.setdefault((task_id, run_id), []).append(str(mismatch.get("type") or "mismatch"))
        mismatch_detail_index.setdefault((task_id, run_id), []).append(mismatch)
    queue_map = {str(event.get("id") or ""): event for event in queue_events}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "")
        task = task_map.get(task_id, {})
        if not task and worker.get("task_source") == "handoff" and isinstance(worker.get("handoff"), dict):
            handoff = worker["handoff"]
            task = {
                "id": task_id,
                "title": "Pending handoff",
                "summary_zh": handoff.get("message"),
                "next": handoff.get("message"),
                "status": handoff.get("status") or "pending",
                "owner": handoff.get("to"),
                "reviewer": handoff.get("from"),
                "source_plane": "handoff",
                "source_ref": {"handoff_from": handoff.get("from"), "handoff_to": handoff.get("to")},
            }
        queue_event = queue_map.get(str(worker.get("queue_event_id") or ""), {})
        linked_mismatches = mismatch_detail_index.get((task_id, str(worker.get("run_id") or "")), [])
        worker_task_links.append(
            {
                "task_id": task_id or None,
                "task_title": task.get("title"),
                "task_summary": task.get("summary_zh"),
                "task_next": task.get("next"),
                "task_status": task.get("status"),
                "owner": task.get("owner"),
                "reviewer": task.get("reviewer"),
                "expected_actor": expected_task_actor(task) if task else None,
                "source_plane": task.get("source_plane"),
                "source_ref": normalized_source_ref(task),
                "worker_run_id": worker.get("run_id"),
                "queue_event_id": worker.get("queue_event_id"),
                "queue_status": queue_event.get("status"),
                "queue_last_event_at": queue_event.get("last_event_at"),
                "actor": worker.get("actor"),
                "provider": worker.get("provider"),
                "task_source": worker.get("task_source"),
                "worker_status": worker.get("status"),
                "runtime_bucket": worker.get("bucket"),
                "dispatch_reason": worker.get("reason"),
                "mode": worker.get("dispatch_mode"),
                "delivery_mode": worker.get("delivery_mode"),
                "last_event_at": worker.get("last_event_at"),
                "last_error": worker.get("last_error"),
                "mismatch_flags": mismatch_index.get((task_id, str(worker.get("run_id") or "")), []),
                "mismatch_count": len(linked_mismatches),
                "resolution_hints": [str(item.get("resolution_hint") or "") for item in linked_mismatches if str(item.get("resolution_hint") or "")],
            }
        )

    proposed = planning.get("proposed_execution_tasks") or []
    materialized_count = int(bridge_summary.get("materialized_count") or 0)
    runtime_mode = str(planning.get("runtime_mode") or "supervisor_managed_execution")
    planning_status = str(planning.get("status") or "inactive")
    supervisor_focus = str(supervisor_state.get("focus_mode") or "").strip()
    if supervisor_focus in {"planning", "execution"}:
        focus_mode = supervisor_focus
        focus_mode_source = "supervisor"
    else:
        focus_mode = "planning" if planning_status in {"active", "human_required"} else "execution" if runtime_mode == "supervisor_managed_execution" else "execution"
        focus_mode_source = "planning_state_fallback"

    live_worker_queue_ids = {str(worker.get("queue_event_id") or "") for worker in live_workers if str(worker.get("queue_event_id") or "")}
    computed_mode_occupancy = {
        "planning": {"running": 0, "pending": 0, "queued": 0},
        "execution": {"running": 0, "pending": 0, "queued": 0},
        "coordination": {"running": 0, "pending": 0, "queued": 0},
        "chair_review": {"running": 0, "pending": 0, "queued": 0},
    }
    dispatch_mode_map = {
        "discussion_planning": "planning",
        "execution": "execution",
        "coordination": "coordination",
        "chair_review": "chair_review",
    }
    for worker in live_workers:
        mode_name = dispatch_mode_map.get(str(worker.get("dispatch_mode") or "").strip())
        if not mode_name:
            continue
        bucket_name = "running" if worker.get("bucket") == "running" else "pending"
        computed_mode_occupancy[mode_name][bucket_name] += 1
    for event in queue_events:
        event_id = str(event.get("id") or "")
        if event_id and event_id in live_worker_queue_ids:
            continue
        mode_name = dispatch_mode_map.get(str(event.get("dispatch_mode") or "").strip())
        if not mode_name:
            continue
        computed_mode_occupancy[mode_name]["queued"] += 1
    mode_occupancy = computed_mode_occupancy

    chair_rotation = orchestrator.get("chair_rotation") if isinstance(orchestrator.get("chair_rotation"), dict) else {}
    chair_summary = {
        "current_index": int(chair_rotation.get("current_index") or 0),
        "last_chair_agent": chair_rotation.get("last_chair_agent"),
        "last_chair_run_at": chair_rotation.get("last_chair_run_at"),
        "last_chair_reason": chair_rotation.get("last_chair_reason"),
        "last_review_path": chair_rotation.get("last_review_path"),
        "last_review_summary": chair_rotation.get("last_review_summary") or [],
        "pending_review_path": chair_rotation.get("pending_review_path"),
        "pending_review_agent": chair_rotation.get("pending_review_agent"),
        "sidecar_approved_until": chair_rotation.get("sidecar_approved_until"),
    }

    lanes: dict[str, dict[str, int]] = {}
    for worker in workers:
        actor = str(worker.get("actor") or "-")
        lane = lanes.setdefault(actor, {"running": 0, "pending": 0, "transition": 0, "completed": 0, "failed": 0})
        bucket = str(worker.get("bucket") or "pending")
        if bucket in {"running", "pending"} and not worker.get("is_live_runtime"):
            continue
        lane[bucket] = lane.get(bucket, 0) + 1
        if worker.get("status") == "failed":
            lane["failed"] += 1

    dispatch_targets = {name: meta["target_workload"] for name, meta in KNOWN_AGENTS.items()}

    sprint_started_at_value = str(state.get("sprint_started_at") or "").strip() or None
    completed_in_sprint, superseded_in_sprint = count_terminal_since(sprint_started_at_value)

    bff_consol_archived_ids: list[str] = []
    if ARCHIVE_TASKS_DIR.exists():
        for path in ARCHIVE_TASKS_DIR.glob("BFF-CONSOL-*.json"):
            stem = path.stem
            if stem.endswith("-SIDECAR-BFF-HANDOFF") or stem.endswith("-SIDECAR-ACCEPTANCE") or stem.endswith("-SIDECAR-REVIEW"):
                continue
            bff_consol_archived_ids.append(stem)
    bff_consol_archived_ids.sort()

    return {
        "generated_at": iso_now(),
        "focus_mode": focus_mode,
        "focus_mode_source": focus_mode_source,
        "runtime_summary": {
            "supervisor_pid": supervisor_state.get("pid"),
            "heartbeat_at": supervisor_state.get("last_heartbeat_at") or orchestrator.get("last_heartbeat_at"),
            "queue_depth": len(queue_events),
            "pending_approvals": len(approvals.get("pending") or []),
            "running_workers": sum(1 for worker in live_workers if worker.get("bucket") == "running"),
            "pending_workers": sum(1 for worker in live_workers if worker.get("bucket") == "pending"),
            "mismatch_count": len(mismatches),
            "mode_status": supervisor_state.get("mode_status") or ("active" if focus_mode == "planning" else "idle"),
            "mode_switch_requested": supervisor_state.get("mode_switch_requested"),
            "mode_occupancy": mode_occupancy,
            "lanes": lanes,
            "dispatch_targets": dispatch_targets,
        },
        "execution_summary": {
            "ready_now": ready_now,
            "dependency_ready": dependency_ready,
            "in_progress": in_progress,
            "in_review": in_review,
            "blocked": blocked,
            "review_approved": review_approved,
            "done": done,
            "superseded": superseded,
            "live_attached": sum(1 for linked in live_workers_by_task.values() if any(worker.get("bucket") == "running" for worker in linked)),
            "mismatch_count": len(mismatches),
            "planning_backed_total": int(bridge_summary.get("planning_backed_total") or 0),
            "planning_backed_active": int(bridge_summary.get("planning_backed_active") or 0),
        },
        "planning_summary": {
            "status": planning.get("status"),
            "consensus_status": planning.get("consensus_status"),
            "human_gate_status": planning.get("human_gate_status"),
            "counts": planning.get("counts") or {},
            "materialized_count": materialized_count,
            "proposed_execution_tasks": len(proposed),
            "active_session": planning.get("active_session")
            or {
                "session_id": planning.get("session_id"),
                "planning_dir": planning.get("planning_dir"),
                "session_file": planning.get("session_file"),
                "status": planning.get("status"),
            },
            "recent_sessions": planning.get("recent_sessions") or [],
        },
        "archive_summary": {
            "updated_at": archive_index.get("updated_at"),
            "counts": {
                "total": int(archive_counts.get("total") or 0),
                "completed": done,
                "superseded": superseded,
                "completed_in_sprint": completed_in_sprint,
                "superseded_in_sprint": superseded_in_sprint,
            },
            "sprint_started_at": sprint_started_at_value,
            "recent_terminal_ids": archive_index.get("recent_terminal_ids") or [],
            "recent_terminal_tasks": recent_terminal_tasks,
            "bff_consol_archived_ids": bff_consol_archived_ids,
        },
        "coordination_summary": coordination_summary,
        "bridge_summary": bridge_summary,
        "chair_summary": chair_summary,
        "dispatch_policy": dispatch_policy,
        "recent_helper_claims": recent_helper_claims(),
        "worker_task_links": worker_task_links,
        "truth_mismatches": mismatches,
    }


def write_dashboard_bundle(state: dict[str, Any]) -> None:
    config = load_config()
    planning_state = load_planning_state()
    try:
        orchestrator_state = load_runtime_state(config)
    except KeyError:
        orchestrator_state = {}
    approval_state = load_json_file(APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
    bundle = build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)
    DASHBOARD_BUNDLE_FILE.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


DASHBOARD_LOG_TAIL_LINES = 5000


def _mirror_log_tail(source: Path, target: Path, max_lines: int) -> None:
    if not source.exists():
        return
    try:
        with source.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            file_size = handle.tell()
            block_size = 1 << 16
            buffer = bytearray()
            line_count = 0
            position = file_size
            while position > 0 and line_count <= max_lines:
                read_size = min(block_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                buffer[0:0] = chunk
                line_count = buffer.count(b"\n")
            tail = bytes(buffer)
        if line_count > max_lines:
            split_at = -1
            extra = line_count - max_lines
            for _ in range(extra):
                split_at = tail.find(b"\n", split_at + 1)
                if split_at == -1:
                    break
            if split_at != -1:
                tail = tail[split_at + 1 :]
        target.write_bytes(tail)
    except OSError:
        return


def dashboard_orchestrator_state(state: dict[str, Any], orchestrator_state: dict[str, Any]) -> dict[str, Any]:
    dashboard_state = deepcopy(orchestrator_state)
    dashboard_workers = dashboard_state.setdefault("workers", {})
    for worker in normalize_runtime_workers(state, orchestrator_state):
        run_id = str(worker.get("run_id") or "").strip()
        if not run_id or run_id not in dashboard_workers:
            continue
        dashboard_workers[run_id]["pid_alive"] = worker.get("pid_alive")
        dashboard_workers[run_id]["pid_state"] = worker.get("pid_state")
        dashboard_workers[run_id]["is_live_runtime"] = worker.get("is_live_runtime")
        dashboard_workers[run_id]["runtime_bucket"] = worker.get("bucket")
        dashboard_workers[run_id]["dispatch_mode"] = worker.get("dispatch_mode")
    return dashboard_state


def sync_docs_site(state: dict[str, Any]) -> None:
    DOCS_SITE_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    try:
        runtime_state = load_runtime_state(config)
    except KeyError:
        runtime_state = {}
    mirror_files = [
        STATUS_FILE,
        CURRENT_WORK_FILE,
        DASHBOARD_BUNDLE_FILE,
        ORCHESTRATOR_STATE_FILE,
        APPROVAL_QUEUE_FILE,
        PLANNING_STATE_FILE,
    ]
    rename_map = {
        "state.json": "orchestrator-state.json",
        "approval-queue.json": "approval-queue.json",
    }
    for path in mirror_files:
        if path.exists():
            target_name = rename_map.get(path.name, path.name)
            if path.name == "state.json":
                dashboard_state = dashboard_orchestrator_state(state, runtime_state)
                (DOCS_SITE_DIR / target_name).write_text(
                    json.dumps(dashboard_state, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            else:
                shutil.copy2(path, DOCS_SITE_DIR / target_name)
    _mirror_log_tail(LOG_FILE, DOCS_SITE_DIR / LOG_FILE.name, DASHBOARD_LOG_TAIL_LINES)


def sync_all(state: dict[str, Any]) -> None:
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    prune_archived_active_tasks(state)
    validate_state(state)
    normalize_handoffs(state)
    recompute_agents(state)
    recompute_workload(state)
    ensure_sprint_started_at(state)
    state["updated_at"] = iso_now()
    save_state(state)
    logs = load_logs()
    write_current_work(state, logs)
    write_dashboard_bundle(state)
    sync_docs_site(state)


def mark_blockers_resolved(state: dict[str, Any], task_id: str) -> None:
    for blocker in state.get("blockers", []):
        if blocker["task_id"] == task_id and blocker["status"] == "open":
            blocker["status"] = "resolved"
            blocker["resolved_at"] = iso_now()


def mark_handoffs_done(state: dict[str, Any], task_id: str) -> None:
    for handoff in state.get("handoffs", []):
        if handoff["task_id"] == task_id and handoff["status"] != "done":
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()


def mark_handoffs_done_for_actor(state: dict[str, Any], task_id: str, actor: str) -> None:
    for handoff in state.get("handoffs", []):
        if handoff["task_id"] == task_id and handoff.get("to") == actor and handoff["status"] != "done":
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()


def normalize_handoffs(state: dict[str, Any]) -> None:
    task_map = {task["id"]: task for task in state["tasks"]}
    pending_by_task: dict[str, list[dict[str, Any]]] = {}
    for handoff in state.get("handoffs", []):
        if handoff.get("status") == "done":
            continue
        pending_by_task.setdefault(handoff["task_id"], []).append(handoff)

    for task_id, pending in pending_by_task.items():
        task = task_map.get(task_id)
        if task:
            task_status = task.get("status")
            if task_status in {"in_progress", "blocked", "done"}:
                for handoff in pending:
                    handoff["status"] = "done"
                    handoff["resolved_at"] = iso_now()
                continue
            if task_status == "review_approved":
                owner = canonical_agent_name(task.get("owner"))
                owner_handoffs = [handoff for handoff in pending if handoff.get("to") == owner]
                for handoff in pending:
                    if handoff not in owner_handoffs:
                        handoff["status"] = "done"
                        handoff["resolved_at"] = iso_now()
                if not owner_handoffs:
                    ensure_review_finalize_handoff(
                        state,
                        task,
                        from_agent=canonical_agent_name(task.get("reviewer")),
                        timestamp=iso_now(),
                        message=task.get("next"),
                    )
                continue

        for handoff in pending[:-1]:
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()

    for task in state.get("tasks", []):
        if task.get("status") != "review_approved":
            continue
        task_id = task.get("id")
        owner = canonical_agent_name(task.get("owner"))
        pending = [
            handoff
            for handoff in state.get("handoffs", [])
            if handoff.get("task_id") == task_id and handoff.get("status") != "done"
        ]
        owner_handoffs = [handoff for handoff in pending if handoff.get("to") == owner]
        for handoff in pending:
            if handoff not in owner_handoffs:
                handoff["status"] = "done"
                handoff["resolved_at"] = iso_now()
        if not owner_handoffs:
            ensure_review_finalize_handoff(
                state,
                task,
                from_agent=canonical_agent_name(task.get("reviewer")),
                timestamp=iso_now(),
                message=task.get("next"),
            )


def command_assign(state: dict[str, Any], args: list[str]) -> None:
    from wave_guards import WaveGuardError, check_wave_assign

    if len(args) < 3:
        raise SystemExit("Usage: assign <task-id> <owner> <reviewer> [title]")
    actor = current_actor_validated()
    try:
        check_wave_assign(state.get("wave_state") or {})
    except WaveGuardError as exc:
        raise SystemExit(f"Wave guard rejected assign: {exc}") from exc
    task_id = args[0]
    owner = resolve_actor_reference(args[1], field="owner")
    reviewer = resolve_actor_reference(args[2], field="reviewer")
    title = args[3] if len(args) > 3 else os.environ.get("TASK_TITLE")
    summary_zh = os.environ.get("TASK_SUMMARY_ZH")
    metadata = task_metadata_from_env()
    if owner == reviewer:
        raise SystemExit("Reviewer cannot equal owner")

    task = get_task(state, task_id)
    timestamp = iso_now()
    if task is None:
        if archived_task_snapshot(task_id):
            raise SystemExit(
                f"Task {task_id} is archived. Create a new follow-up task instead of reusing the archived task id."
            )
        task = {
            "id": task_id,
            "title": title,
            "summary_zh": summary_zh,
            "phase": os.environ.get("TASK_PHASE", "Unassigned"),
            "owner": owner,
            "reviewer": reviewer,
            "status": "todo",
            "depends_on": parse_csv_env("TASK_DEPENDS_ON"),
            "artifacts": parse_csv_env("TASK_ARTIFACTS"),
            "acceptance": parse_csv_env("TASK_ACCEPTANCE"),
            "next": "Assignment created",
            "last_update": timestamp,
        }
        task.update(metadata)
        state["tasks"].append(task)
    else:
        task["owner"] = owner
        task["reviewer"] = reviewer
        if title:
            task["title"] = title
        if summary_zh:
            task["summary_zh"] = summary_zh
        if metadata:
            task.update(metadata)
        task["last_update"] = timestamp
        task["next"] = "Ownership updated"

    agent = get_agent(state, owner)
    if os.environ.get("TASK_BRANCH"):
        agent["branch"] = os.environ["TASK_BRANCH"]

    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "assign",
            "task_id": task_id,
            "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
        }
    )


def command_start(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: start <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can start {task_id}")
    timestamp = iso_now()
    task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    mark_blockers_resolved(state, task_id)
    append_log({"ts": timestamp, "agent": actor, "type": "start", "task_id": task_id, "message": message})


def command_progress(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: progress <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can progress {task_id}")
    timestamp = iso_now()
    if task["status"] in {"todo", "review_approved"}:
        task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    append_log({"ts": timestamp, "agent": actor, "type": "progress", "task_id": task_id, "message": message})


def command_note(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: note <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    timestamp = iso_now()
    task["last_update"] = timestamp
    task["next"] = message
    append_log({"ts": timestamp, "agent": actor, "type": "note", "task_id": task_id, "message": message})


def command_reopen(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: reopen <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        if archived_task_snapshot(task_id):
            raise SystemExit(
                f"Task {task_id} is archived and cannot be reopened in place. Create a new follow-up task that references {task_id}."
            )
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if actor not in {owner, reviewer}:
        raise SystemExit(f"Only the owner ({owner}) or reviewer ({reviewer}) can reopen {task_id}")
    timestamp = iso_now()
    task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)
    task.pop("approved_head", None)
    task["last_reopened_by"] = actor
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    if actor == reviewer and owner and owner != reviewer:
        state.setdefault("handoffs", []).append(
            {
                "task_id": task_id,
                "from": reviewer,
                "to": owner,
                "message": message,
                "status": "pending",
                "created_at": timestamp,
            }
        )
    append_log({"ts": timestamp, "agent": actor, "type": "reopen", "task_id": task_id, "message": message})


def command_handoff(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: handoff <task-id> <to-agent> <message>")
    task_id, message = args[0], args[2]
    to_agent = resolve_actor_reference(args[1], field="handoff target")
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can hand off {task_id} for review")
    if task.get("reviewer") != to_agent:
        raise SystemExit(
            f"{task_id} handoff target must match the assigned reviewer ({task.get('reviewer')}); reassign reviewer first if needed"
        )
    timestamp = iso_now()
    task["status"] = "review"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("approved_head", None)
    mark_handoffs_done_for_actor(state, task_id, actor)
    mark_blockers_resolved(state, task_id)
    state.setdefault("handoffs", []).append(
        {
            "task_id": task_id,
            "from": actor,
            "to": to_agent,
            "message": message,
            "status": "pending",
            "created_at": timestamp,
        }
    )
    append_log({"ts": timestamp, "agent": actor, "type": "handoff", "task_id": task_id, "message": f"Handoff to {to_agent}: {message}"})


def command_re_review(state: dict[str, Any], args: list[str]) -> None:
    """re_review <task-id> <message>"""
    if len(args) < 2:
        raise SystemExit("Usage: re_review <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if actor not in {owner, reviewer}:
        raise SystemExit(f"Only the owner ({owner}) or reviewer ({reviewer}) can request re-review for {task_id}")
    timestamp = iso_now()
    task["status"] = "review"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("approved_head", None)
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    if owner and reviewer and owner != reviewer:
        state.setdefault("handoffs", []).append(
            {
                "task_id": task_id,
                "from": owner,
                "to": reviewer,
                "message": message,
                "status": "pending",
                "created_at": timestamp,
            }
        )
    append_log({"ts": timestamp, "agent": actor, "type": "re_review", "task_id": task_id, "message": message})


def command_blocker(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: blocker <task-id> <message> <waiting-for>")
    task_id, message = args[0], args[1]
    waiting_for = resolve_actor_reference(args[2], field="waiting-for")
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can block {task_id}")
    timestamp = iso_now()
    task["status"] = "blocked"
    task["waiting_for"] = waiting_for
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    state.setdefault("blockers", []).append(
        {
            "task_id": task_id,
            "owner": actor,
            "waiting_for": waiting_for,
            "message": message,
            "status": "open",
            "created_at": timestamp,
        }
    )
    append_log({"ts": timestamp, "agent": actor, "type": "blocker", "task_id": task_id, "message": f"Blocked on {waiting_for}: {message}"})


def command_retarget_blocker(state: dict[str, Any], args: list[str]) -> None:
    """Repoint a blocker's waiting_for at a real agent without losing its text.

    Usage: retarget_blocker <task-id> <agent> <reason> [--index N]

    Allowed when the current waiting_for is not a usable actor reference (a
    repair), or when the caller owns the blocker (a normal reassignment). This
    keeps the command from becoming a way to silently reassign someone else's
    valid blocker. `--index N` selects a single blocker of that task by its
    position in `blockers[]` when the task has several.
    """
    positional = [arg for arg in args if not arg.startswith("--")]
    if len(positional) < 3:
        raise SystemExit("Usage: retarget_blocker <task-id> <agent> <reason> [--index N]")
    task_id, reason = positional[0], positional[2]
    new_actor = resolve_actor_reference(positional[1], field="waiting-for")
    actor = current_actor_validated()

    selected_index: int | None = None
    for arg in args:
        if arg.startswith("--index="):
            selected_index = int(arg.split("=", 1)[1])

    targets = [
        blocker
        for index, blocker in enumerate(state.get("blockers", []))
        if blocker.get("task_id") == task_id
        and (selected_index is None or index == selected_index)
    ]
    if not targets:
        raise SystemExit(f"No blocker recorded for task: {task_id}")

    timestamp = iso_now()
    changed: list[dict[str, Any]] = []
    for blocker in targets:
        previous = str(blocker.get("waiting_for") or "")
        if previous == new_actor:
            continue
        repairable = actor_reference_problem(canonical_agent_name(previous)) is not None
        if not repairable and blocker.get("owner") != actor:
            # Already a real agent and not ours to reassign — leave it untouched.
            continue
        blocker["waiting_for"] = new_actor
        blocker["original_waiting_for"] = previous
        blocker["retargeted_by"] = actor
        blocker["retargeted_at"] = timestamp
        blocker["retarget_reason"] = reason
        if repairable and previous and previous not in str(blocker.get("message") or ""):
            existing = str(blocker.get("message") or "").strip()
            blocker["message"] = (
                f"{existing}\n\nRecovered waiting_for text: {previous}" if existing else previous
            )
        changed.append(blocker)

    task = get_task(state, task_id)
    task_repaired = False
    if task is not None:
        current = str(task.get("waiting_for") or "")
        if current and actor_reference_problem(canonical_agent_name(current)) is not None:
            task["waiting_for"] = new_actor
            task["last_update"] = timestamp
            task_repaired = True

    if not changed and not task_repaired:
        print(
            f"No change for {task_id}: nothing to repair, and any valid blocker "
            f"waiting_for belongs to another owner"
        )
        return

    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "actor_ref_repair",
            "task_id": task_id,
            "message": (
                f"Retargeted {len(changed)} blocker record(s)"
                f"{' and task waiting_for' if task_repaired else ''} to {new_actor}: {reason}"
            ),
        }
    )
    print(
        f"Retargeted {len(changed)} blocker record(s)"
        f"{' and task waiting_for' if task_repaired else ''} for {task_id} to {new_actor}"
    )


def synthetic_roster_entries(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify every durable roster entry as keep-or-remove for the cleanup path.

    An entry is removable only when it is (a) not declared in the merged
    Supervisor config, (b) not an explicit non-worker actor, (c) not carrying
    live workload, and (d) not referenced by any task, blocker or handoff. A
    valid actor is never removed just for being idle.
    """
    referenced: set[str] = set()

    def mark(value: Any) -> None:
        canonical = canonical_agent_name(value)
        if canonical:
            referenced.add(canonical)

    for task in state.get("tasks", []):
        mark(task.get("owner"))
        mark(task.get("reviewer"))
        mark(task.get("waiting_for"))
    for blocker in state.get("blockers", []):
        mark(blocker.get("owner"))
        mark(blocker.get("waiting_for"))
    for handoff in state.get("handoffs", []):
        mark(handoff.get("from"))
        mark(handoff.get("to"))

    configured = configured_agent_names()
    extras = extra_actor_names()
    report: list[dict[str, Any]] = []
    for agent in state.get("agents", []) or []:
        name = canonical_agent_name(agent.get("name"))
        if name in configured:
            reason = "declared in the merged Supervisor config (config.json + config.local.json)"
        elif name in NON_WORKER_ACTORS:
            reason = "explicitly declared non-worker actor"
        elif name in extras:
            reason = "registered through AI_STATUS_EXTRA_AGENTS"
        elif name in BASELINE_KNOWN_AGENT_NAMES:
            # Not an authority for validation — resolve_actor_reference still
            # rejects these — but recompute_agents() recreates a row for every
            # static lane, so reporting them as removable would be a lie.
            reason = (
                "static KNOWN_AGENTS lane; recompute_agents recreates the row, "
                "so pruning it is a no-op (still not accepted as an actor reference)"
            )
        elif agent.get("current_task_ids") or str(agent.get("status") or "") not in ("", "idle"):
            reason = "carrying live workload; repoint its work before pruning it"
        elif name in referenced:
            reason = "still referenced by a task, blocker or handoff"
        else:
            problem = actor_reference_problem(name)
            report.append(
                {
                    "name": name,
                    "keep": False,
                    "reason": problem or "undeclared and unreferenced synthetic roster entry",
                }
            )
            continue
        report.append({"name": name, "keep": True, "reason": reason})
    return report


def command_prune_agents(state: dict[str, Any], args: list[str]) -> None:
    """Audited removal of unreferenced synthetic roster entries.

    Usage: prune_agents [--apply] [reason]

    Dry-run by default. Never removes a declared or still-referenced actor.
    """
    apply = "--apply" in args
    reason = next((arg for arg in args if not arg.startswith("--")), "actor reference cleanup")
    actor = current_actor_validated()

    report = synthetic_roster_entries(state)
    removable = [entry for entry in report if not entry["keep"]]

    for entry in report:
        verb = "KEEP  " if entry["keep"] else "REMOVE"
        preview = entry["name"] if len(entry["name"]) <= 70 else entry["name"][:67] + "..."
        print(f"{verb} {preview!r} — {entry['reason']}")

    if not removable:
        print("\nNothing to prune: every roster entry is declared or still referenced.")
        return
    if not apply:
        print(f"\nDry run: {len(removable)} entry(ies) would be removed. Re-run with --apply.")
        return

    doomed = {entry["name"] for entry in removable}
    state["agents"] = [
        agent
        for agent in state.get("agents", [])
        if canonical_agent_name(agent.get("name")) not in doomed
    ]
    for name in doomed:
        KNOWN_AGENTS.pop(name, None)
        QUARANTINED_AGENTS.discard(name)
        state.get("workload", {}).pop(name, None)
        state.get("workload_summary", {}).pop(name, None)

    timestamp = iso_now()
    for entry in removable:
        append_log(
            {
                "ts": timestamp,
                "agent": actor,
                "type": "agent_pruned",
                "task_id": "",
                "message": f"Removed synthetic roster entry {entry['name']!r}: {entry['reason']} ({reason})",
            }
        )
    print(f"\nRemoved {len(removable)} synthetic roster entry(ies).")


def command_restore_approved(state: dict[str, Any], args: list[str]) -> None:
    """Recover a task that was incorrectly downgraded from review_approved to in_progress by the supervisor.

    Only allowed when:
    - actor is the owner
    - current status is in_progress
    - task has review_notes_zh (evidence of a prior approval)
    """
    if len(args) < 2:
        raise SystemExit("Usage: restore_approved <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can restore {task_id}")
    if task.get("status") != "in_progress":
        raise SystemExit(f"restore_approved is only valid when status is in_progress (current: {task.get('status')})")
    if not task.get("review_notes_zh"):
        raise SystemExit(
            "restore_approved requires review_notes_zh to be present as evidence of a prior approval. "
            "Use the normal review lifecycle if the task has not been reviewed yet."
        )

    # B21: this command is the second producer of `review_approved`, so it has to
    # re-establish the freeze or it manufactures exactly the un-frozen state the
    # freeze exists to prevent. It must NOT resolve the current head itself --
    # this is an owner-invokable command, and resolving here would let the owner
    # self-freeze a head no reviewer ever saw. It may only re-freeze the durable
    # `last_approved_head` written by `command_approve`, and only while the branch
    # still sits on it.
    last_approved_head = task.get("last_approved_head")
    if not last_approved_head:
        raise SystemExit(
            f"Cannot restore {task_id}: no durable reviewer-approved head is recorded, so the "
            "post-review freeze cannot be re-established and restoring would finalize at an "
            f"unreviewed commit. Run `re_review {task_id} <reason>` so the reviewer re-stamps "
            "the head. No restore was recorded."
        )
    reviewer = canonical_agent_name(task.get("reviewer"))
    last_reopened_by = canonical_agent_name(task.get("last_reopened_by"))
    pending_handoffs = state.get("handoffs", [])
    has_pending_reviewer_handoff = any(
        str(h.get("task_id") or "").upper() == str(task_id).upper()
        and h.get("status") == "pending"
        and canonical_agent_name(h.get("from")) == reviewer
        for h in pending_handoffs
    )
    if has_pending_reviewer_handoff or (last_reopened_by and last_reopened_by == reviewer):
        raise SystemExit(
            f"Cannot restore {task_id}: the task was reopened by the reviewer ({reviewer}). "
            "Reviewer rejections cannot be restored by the owner; run `re_review` so the "
            "reviewer can re-examine the work. No restore was recorded."
        )
    try:
        current_sha = resolve_task_sha(task_id, force_refresh=True)
    except Exception as exc:
        raise SystemExit(
            f"Cannot restore {task_id}: unable to resolve the current branch HEAD ({exc}). "
            "Integrity gate failed closed; no restore was recorded."
        ) from exc
    if not current_sha:
        raise SystemExit(
            f"Cannot restore {task_id}: the branch HEAD could not be resolved, so it cannot be "
            f"checked against the reviewer-approved head ({last_approved_head[:8]}). "
            "No restore was recorded."
        )
    if current_sha != last_approved_head:
        raise SystemExit(
            f"Cannot restore {task_id}: the branch has moved to {current_sha[:8]}, past the "
            f"reviewer-approved head ({last_approved_head[:8]}). The downgrade was not spurious. "
            f"Run `re_review {task_id} <reason>` so the reviewer stamps the new head. "
            "No restore was recorded."
        )

    timestamp = iso_now()
    task["status"] = "review_approved"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("last_reopened_by", None)
    task["approved_head"] = last_approved_head
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "restore_approved",
            "task_id": task_id,
            "message": message,
            "approved_head": last_approved_head,
        }
    )


def command_restore_approved_head(state: dict[str, Any], args: list[str]) -> None:
    """restore_approved_head <task-id> <sha> <message>

    B22: `review_approved` with no `approved_head` fails closed everywhere -- in
    `command_done` and in both supervisor dispatch gates. Pre-freeze tasks and
    tasks approved by an older build sit in exactly that shape, so they need a way
    out; making it an automatic bypass is what the freeze exists to stop. This is
    that way out as an explicit, audited, reviewer-signed attestation of one exact
    commit: the reviewer names the sha, it must match the immutable PR head, and
    the restoration is written to the activity log.
    """
    if len(args) < 3:
        raise SystemExit("Usage: restore_approved_head <task-id> <sha> <message>")
    task_id, requested_sha, message = args[0], args[1].strip(), args[2]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if owner and reviewer and owner == reviewer:
        raise SystemExit(
            f"Owner ({owner}) and reviewer ({reviewer}) must be separate identities for task {task_id}"
        )
    if reviewer != actor:
        raise SystemExit(
            f"Only the reviewer ({reviewer}) can attest the approved head for {task_id}; "
            "the owner cannot restore the freeze on their own work."
        )
    if task.get("status") != "review_approved":
        raise SystemExit(
            f"restore_approved_head is only valid when status is review_approved "
            f"(current: {task.get('status')}). Use `approve` for the normal review lifecycle."
        )
    existing_head = task.get("approved_head")
    if existing_head:
        raise SystemExit(
            f"Cannot restore the approved head for {task_id}: it already carries one "
            f"({existing_head[:8]}). This command only repairs the missing-head shape."
        )
    if not re.fullmatch(r"[0-9a-f]{40}", requested_sha):
        raise SystemExit(
            f"Cannot restore the approved head for {task_id}: {requested_sha!r} is not a full "
            "40-character commit sha. Name the exact reviewed commit, not an abbreviation."
        )
    try:
        current_sha = resolve_task_sha(task_id, force_refresh=True)
    except Exception as exc:
        raise SystemExit(
            f"Cannot restore the approved head for {task_id}: unable to resolve the branch HEAD "
            f"({exc}). Integrity gate failed closed; nothing was recorded."
        ) from exc
    if not current_sha:
        raise SystemExit(
            f"Cannot restore the approved head for {task_id}: the branch HEAD could not be "
            "resolved, so the attested sha cannot be corroborated. Nothing was recorded."
        )
    _head_ref_oid, merge_commit = task_pr_head_and_merge_commit(task_id)
    if merge_commit and requested_sha == merge_commit:
        raise SystemExit(
            f"Cannot restore the approved head for {task_id}: {requested_sha[:8]} is the PR merge "
            "commit, not the reviewed branch head. Attest the head the review actually read."
        )
    if requested_sha != current_sha:
        raise SystemExit(
            f"Cannot restore the approved head for {task_id}: the attested sha ({requested_sha[:8]}) "
            f"does not match the task branch head ({current_sha[:8]}). Only the exact current head "
            f"can be restored; run `re_review {task_id} <reason>` if the branch has moved."
        )

    timestamp = iso_now()
    task["approved_head"] = requested_sha
    task["last_approved_head"] = requested_sha
    task["last_update"] = timestamp
    task["next"] = message
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "approved_head_restored",
            "task_id": task_id,
            "message": message,
            "approved_head": requested_sha,
        }
    )


def command_done(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: done <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can finalize {task_id} to done")
    if task.get("status") != "review_approved":
        raise SystemExit(f"{task_id} must be review_approved before it can move to done")
    # B22: the freeze gate runs here, before collect_done_delivery_metadata, and
    # every branch of it raises. The delivery gates further down are real
    # defence-in-depth but they are not a backstop for this gate: they check
    # merge/commit hygiene, not "is this the commit a reviewer read".
    approved_head = task.get("approved_head")
    if not approved_head:
        raise SystemExit(
            f"Cannot finalize task {task_id}: it is review_approved but carries no "
            "reviewer-approved head, so there is nothing to verify the branch against. "
            "This is the pre-freeze (or restored) shape and it fails closed. "
            f"The reviewer must run `restore_approved_head {task_id} <sha> <reason>` to "
            f"attest the exact reviewed commit, or `re_review {task_id} <reason>` for a "
            "fresh review. No finalization was recorded."
        )
    timestamp = iso_now()
    delivery = collect_done_delivery_metadata(task, actor, approved_head=approved_head)
    # The task branch is ephemeral and GitHub may delete its remote ref as soon
    # as the PR merges.  Do not make that deleted ref a second closeout
    # authority.  The collector resolves the unique task-owned checkout and
    # verifies the immutable merged PR instead; keep both heads exact here as
    # defence in depth even when the collector is replaced by a unit-test fake.
    current_sha = str(delivery.get("verified_head") or "").strip()
    if current_sha != approved_head:
        if not delivery.get("post_merge_checkout_advanced"):
            display_sha = current_sha[:8] if current_sha else "unresolved"
            raise SystemExit(
                f"Cannot finalize task {task_id}: task-owned checkout HEAD ({display_sha}) "
                f"differs from reviewer-approved head ({approved_head[:8]}). "
                "Strict update-branch merge requires re-review."
            )

    pull_request_raw = delivery.get("pull_request")
    pull_request = pull_request_raw if isinstance(pull_request_raw, dict) else {}
    head_ref_oid = str(pull_request.get("head_sha") or "").strip()
    merge_commit = str(pull_request.get("merge_commit") or "").strip()
    if head_ref_oid != approved_head:
        display_sha = head_ref_oid[:8] if head_ref_oid else "unresolved"
        raise SystemExit(
            f"Cannot finalize task {task_id}: merged PR headRefOid ({display_sha}) "
            f"differs from reviewer-approved head ({approved_head[:8]}). "
            "Immutable approved-head PR provenance failed closed."
        )

    delivery["approved_head"] = approved_head
    delivery["verified_head"] = current_sha
    delivery["pr_head_ref_oid"] = head_ref_oid
    if merge_commit:
        delivery["pr_merge_commit"] = merge_commit
    delivery["recorded_at"] = timestamp
    task["status"] = "done"
    task["terminal_outcome"] = "completed"
    task["last_update"] = timestamp
    task["next"] = message
    task["delivery"] = delivery
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    archive_terminal_task_from_state(state, task, archived_at=timestamp)
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "done",
            "task_id": task_id,
            "message": message,
            "delivery": delivery,
        }
    )


def command_supersede(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: supersede <task-id> <message> [replacement-task-id]")
    task_id, message = args[0], args[1]
    replacement_task_id = args[2].strip() if len(args) > 2 and args[2].strip() else ""
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if actor not in {owner, reviewer}:
        raise SystemExit(f"Only the owner ({owner}) or reviewer ({reviewer}) can supersede {task_id}")
    timestamp = iso_now()
    task["status"] = "done"
    task["terminal_outcome"] = TASK_TERMINAL_SUPERSEDED
    task["last_update"] = timestamp
    task["next"] = message
    if replacement_task_id:
        task["superseded_by"] = replacement_task_id
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    archive_terminal_task_from_state(state, task, archived_at=timestamp)
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "superseded",
            "task_id": task_id,
            "message": message,
            **({"replacement_task_id": replacement_task_id} if replacement_task_id else {}),
        }
    )


def command_approve(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: approve <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor_validated()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if owner and reviewer and owner == reviewer:
        raise SystemExit(f"Owner ({owner}) and reviewer ({reviewer}) must be separate identities for task {task_id}")
    if task.get("reviewer") != actor:
        raise SystemExit(f"Only the reviewer ({task.get('reviewer')}) can approve {task_id}")
    if task.get("status") != "review":
        raise SystemExit(f"{task_id} must be in review before it can move to review_approved")

    # B20: the approved head is the whole basis of the post-review freeze.
    # command_done and both supervisor dispatch gates are guarded by
    # `if approved_head:`, so approving without recording one silently disables
    # the freeze for that task instead of failing closed. Resolve it *before*
    # any mutation, so an unresolvable head aborts the approval outright rather
    # than leaving the task review_approved-but-unfrozen.
    try:
        approved_sha = resolve_task_sha(task_id, force_refresh=True)
    except Exception as exc:
        raise SystemExit(
            f"Cannot approve task {task_id}: unable to resolve the branch HEAD to freeze ({exc}). "
            "Integrity gate failed closed; no approval was recorded."
        ) from exc
    if not approved_sha:
        raise SystemExit(
            f"Cannot approve task {task_id}: branch HEAD could not be resolved, so the "
            "reviewer-approved commit cannot be frozen. Push the task branch (or open its PR) "
            "and approve again. No approval was recorded."
        )

    # B20: approved_head is immutable for the lifetime of one approval. Every
    # transition back to `review` (handoff, reopen, re_review, and the
    # supervisor's head-drift demotion) pops it, so a task sitting in `review`
    # while still carrying an approved_head is inconsistent state. Refuse to
    # overwrite the earlier freeze silently.
    existing_head = task.get("approved_head")
    if existing_head and existing_head != approved_sha:
        raise SystemExit(
            f"Cannot approve task {task_id}: it still carries an uncleared approved head "
            f"({existing_head[:8]}) while the branch is now at {approved_sha[:8]}. "
            f"Run `re_review {task_id} <reason>` to clear the stale freeze first. "
            "No approval was recorded."
        )

    timestamp = iso_now()
    task["status"] = "review_approved"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)
    task.pop("last_reopened_by", None)
    task["approved_head"] = approved_sha
    # B21: `approved_head` is the *live* freeze and every return-to-review pops it.
    # `last_approved_head` is the durable record of the last commit a reviewer
    # actually stamped: it is never popped, so `restore_approved` can re-freeze a
    # spurious supervisor downgrade without the owner getting to name the head.
    task["last_approved_head"] = approved_sha

    review_notes = parse_delimited_env("REVIEW_NOTES_ZH")
    if review_notes:
        task["review_notes_zh"] = review_notes

    review_file = os.environ.get("REVIEW_FILE", "").strip()
    if review_file:
        task["review_file"] = review_file

    mark_blockers_resolved(state, task_id)
    mark_handoffs_done_for_actor(state, task_id, actor)
    ensure_review_finalize_handoff(
        state,
        task,
        from_agent=actor,
        timestamp=timestamp,
        message=message,
    )
    append_log({"ts": timestamp, "agent": actor, "type": "review_approved", "task_id": task_id, "message": message})


def command_sync(state: dict[str, Any], _args: list[str]) -> None:
    return None



def command_archive_migrate(state: dict[str, Any], _args: list[str]) -> None:
    actor = current_actor_validated()
    archived_at = iso_now()
    archived_ids = archive_terminal_tasks_in_state(state, archived_at=archived_at)
    append_log(
        {
            "ts": archived_at,
            "agent": actor,
            "type": "archive_migrate",
            "message": f"Archived {len(archived_ids)} terminal tasks from ai-status.json.",
            "task_ids": archived_ids,
        }
    )


def command_prompt(state: dict[str, Any], _args: list[str]) -> None:
    print(build_onboarding_prompt(state))


def command_show(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 1:
        raise SystemExit("Usage: show <task-id>")
    task_id = args[0]
    active_task = get_task(state, task_id)
    if active_task is not None:
        print(
            json.dumps(
                {
                    "source": "active",
                    "task": active_task,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    snapshot = archived_task_snapshot(task_id)
    if snapshot is None:
        raise SystemExit(f"Unknown task: {task_id}")
    print(
        json.dumps(
            {
                "source": "archive",
                "snapshot_path": archive_display_path(archive_task_path(task_id)),
                "snapshot": snapshot,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def command_wave(state: dict[str, Any], args: list[str]) -> None:
    """wave open <wave-id> | wave close | wave freeze"""
    from wave_guards import (  # lazy: only needed for this command
        WaveGuardError,
        check_wave_close,
        check_wave_freeze,
        check_wave_open,
    )

    if not args:
        raise SystemExit("Usage: wave <open <wave-id> | close | freeze>")

    subcommand = args[0]
    actor = current_actor_validated()
    timestamp = iso_now()
    wave_state: dict[str, Any] = state.setdefault("wave_state", {})
    planning_state = load_planning_state()

    if subcommand == "open":
        if len(args) < 2:
            raise SystemExit("Usage: wave open <wave-id>")
        new_wave_id = args[1]
        try:
            check_wave_open(wave_state, new_wave_id, actor, planning_state)
        except WaveGuardError as exc:
            raise SystemExit(f"Wave guard rejected open: {exc}") from exc
        wave_state["current_wave_id"] = new_wave_id
        wave_state["status"] = "open"
        wave_state["opened_at"] = timestamp
        wave_state["frozen_at"] = None
        wave_state["closed_at"] = None
        wave_state["branch"] = f"wave/{new_wave_id}"
        wave_state.setdefault("history", []).append(
            {"ts": timestamp, "event": "open", "wave_id": new_wave_id, "actor": actor, "branch": f"wave/{new_wave_id}"}
        )
        append_log({"ts": timestamp, "agent": actor, "type": "wave_open", "wave_id": new_wave_id})

    elif subcommand == "close":
        try:
            check_wave_close(wave_state, actor, planning_state)
        except WaveGuardError as exc:
            raise SystemExit(f"Wave guard rejected close: {exc}") from exc
        current_wave_id = wave_state.get("current_wave_id", "")
        wave_state["status"] = "closed"
        wave_state["closed_at"] = timestamp
        wave_state.setdefault("history", []).append(
            {"ts": timestamp, "event": "close", "wave_id": current_wave_id, "actor": actor}
        )
        append_log({"ts": timestamp, "agent": actor, "type": "wave_close", "wave_id": current_wave_id})

    elif subcommand == "freeze":
        try:
            check_wave_freeze(wave_state, actor, planning_state)
        except WaveGuardError as exc:
            raise SystemExit(f"Wave guard rejected freeze: {exc}") from exc
        current_wave_id = wave_state.get("current_wave_id", "")
        wave_state["status"] = "frozen"
        wave_state["frozen_at"] = timestamp
        wave_state.setdefault("history", []).append(
            {"ts": timestamp, "event": "freeze", "wave_id": current_wave_id, "actor": actor}
        )
        append_log({"ts": timestamp, "agent": actor, "type": "wave_freeze", "wave_id": current_wave_id})

    else:
        raise SystemExit(f"Unknown wave subcommand: {subcommand!r}. Use: open <wave-id>, close, freeze")


def resolve_task_sha(
    task_id: str,
    max_age_seconds: float = 5.0,
    force_refresh: bool = False,
    fresh: bool = False,
) -> str | None:
    now = time.time()
    if not (force_refresh or fresh) and max_age_seconds > 0:
        if task_id in _TASK_SHA_CACHE:
            ts, cached_sha = _TASK_SHA_CACHE[task_id]
            if now - ts < max_age_seconds:
                return cached_sha

    branch_names = [f"task/{task_id}", f"task-{task_id}"]

    remote_refs = [f"refs/heads/{branch_name}" for branch_name in branch_names]
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", *remote_refs],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    matches: list[str] = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            fields = line.split()
            if (
                len(fields) == 2
                and fields[1] in remote_refs
                and (
                    re.fullmatch(r"[0-9a-fA-F]{40}", fields[0])
                    or re.fullmatch(r"[0-9a-fA-F]{64}", fields[0])
                )
            ):
                matches.append(fields[0])
    # Fail closed unless origin returns exactly one valid canonical task ref.
    # Local HEAD, local task refs, cached origin refs, and old PR heads are not
    # authoritative active-task review/freeze evidence.
    if result.returncode == 0 and len(matches) == 1:
        _TASK_SHA_CACHE[task_id] = (now, matches[0])
        return matches[0]

    _TASK_SHA_CACHE[task_id] = (now, None)
    return None


def resolve_task_checkout_sha(
    task: dict[str, Any] | str,
    force_refresh: bool = False,
    repository_root: Path | None = None,
) -> str | None:
    """Resolve active remote task branch SHA or post-merge checkout HEAD for a task.

    Returns:
    - The remote task branch SHA from origin/task/<id> (via resolve_task_sha) if present.
    - If origin has no task branch (e.g. deleted after PR auto-merge), checks candidate
      checkout HEADs (local checkout HEAD, target branch SHA, delivery verified_head) and
      returns the first candidate that satisfies `is_approved_head_satisfied`.
    - None if no remote branch exists and no post-merge checkout HEAD satisfies approved_head.
    """
    if isinstance(task, str):
        task_id = task.strip()
        state = load_state()
        task_dict = get_task(state, task_id) or {"id": task_id}
    else:
        task_dict = task
        task_id = str(task_dict.get("id") or "").strip()

    if not task_id:
        return None

    remote_sha = resolve_task_sha(task_id, force_refresh=force_refresh)
    if remote_sha:
        return remote_sha

    approved_head = str(task_dict.get("approved_head") or "").strip()
    if not approved_head:
        return None

    try:
        config = load_config()
        repository_id = task_primary_repository_id(config, task_dict) or "pantheon"
        repo_root = repository_root or repository_local_path(config, repository_id) or ROOT
        repo_root = repo_root.resolve(strict=False)

        candidates: list[str] = []

        local_head = run_git_command(["rev-parse", "HEAD"], cwd=repo_root, required=False)
        if local_head:
            candidates.append(local_head.strip())

        target_branch = delivery_merge_target_branch(config, repository_id)
        remotes_output = run_git_command(["remote"], cwd=repo_root, required=False)
        remote_names = [line.strip() for line in remotes_output.splitlines() if line.strip()]
        if remote_names:
            remote = "origin" if "origin" in remote_names else remote_names[0]
            target_ref = f"{remote}/{target_branch}"
            run_git_command(["fetch", remote, target_branch], cwd=repo_root, required=False)
            target_sha = run_git_command(["rev-parse", "--verify", target_ref], cwd=repo_root, required=False)
            if target_sha:
                candidates.append(target_sha.strip())

        delivery = task_dict.get("delivery")
        if isinstance(delivery, dict):
            v_head = str(delivery.get("verified_head") or "").strip()
            if v_head:
                candidates.append(v_head)

        seen = set()
        unique_candidates: list[str] = []
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        for candidate in unique_candidates:
            if is_approved_head_satisfied(task_dict, candidate, approved_head, repository_root=repo_root):
                return candidate
    except Exception:
        pass

    return None



def task_pr_head_and_merge_commit(task_id: str) -> tuple[str | None, str | None]:
    """Return ``(headRefOid, mergeCommit_oid)`` for the task PR, or ``(None, None)``.

    B22-3: these are two different commits and the distinction has to be explicit.
    ``headRefOid`` is the branch tip the reviewer stamped; it is immutable once the
    PR merges, which is what makes it a usable freeze anchor after merge.
    ``mergeCommit`` is a *new* commit GitHub creates on the target branch, and it
    has never been reviewed. Finalizing against it would let an unreviewed commit
    close the task, so it is recorded as delivery evidence only -- never compared
    against ``approved_head``.
    """
    for branch_name in [f"task/{task_id}", f"task-{task_id}"]:
        result = subprocess.run(
            [get_gh_executable(), "pr", "view", branch_name, "--json", "headRefOid,mergeCommit"],
            capture_output=True,
            text=True,
            check=False,
            cwd=ROOT,
        )
        if result.returncode != 0:
            continue
        try:
            data = json.loads(result.stdout)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        head_ref_oid = data.get("headRefOid") or None
        merge_commit = data.get("mergeCommit") or {}
        merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
        if head_ref_oid or merge_oid:
            return head_ref_oid, (merge_oid or None)
    return None, None


def get_repository_slug_safe() -> str:
    # 1. Try env variable
    env_slug = os.environ.get("GITHUB_REPOSITORY")
    if env_slug:
        return env_slug
    # 2. Try loading config
    try:
        config = load_config()
        slug = repository_slug(config, "pantheon")
        if slug:
            return slug
    except Exception:
        pass
    # 3. Try reading from git remote
    try:
        remote_url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=ROOT,
        ).strip()
        match = re.search(r"github\.com[:/]([^/]+/[^/.]+)(?:\.git)?", remote_url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "alfloop-dev/odayplus"


def task_review_status_payload(task: dict[str, Any], state_status: str) -> dict[str, str] | None:
    task_id = str(task.get("id") or "")
    sha = resolve_task_sha(task_id)
    if not sha:
        return None

    repo_slug = get_repository_slug_safe()
    context = "task-review-gate"

    if state_status == "review_approved":
        approved_head = task.get("approved_head")
        if not approved_head:
            # B22: no frozen head means nothing corroborates that this commit is
            # the reviewed one, so the gate cannot claim success for it.
            state = "pending"
            description = "review_approved but no reviewer-approved head is recorded; head restoration or re-review required"
        elif not is_approved_head_satisfied(task, sha, approved_head):
            state = "pending"
            description = f"Branch HEAD ({sha[:8]}) differs from approved head ({approved_head[:8]}); re-review required"
        else:
            state = "success"
            description = f"Approved by assigned reviewer {task.get('reviewer', 'Codex')}"
    elif state_status == "review":
        state = "pending"
        description = f"Pending review by {task.get('reviewer', 'Codex')}"
    elif state_status == "done":
        state = "success"
        description = "Task completed"
    else:
        state = "failure"
        description = f"Review rejected or reopened. Task status is {state_status}"

    return {
        "repo_slug": repo_slug,
        "sha": sha,
        "state": state,
        "context": context,
        "description": description,
    }


def post_task_review_status_payload(payload: dict[str, str]) -> tuple[bool, str]:
    cmd = [
        get_gh_executable(), "api",
        "-X", "POST",
        f"repos/{payload['repo_slug']}/statuses/{payload['sha']}",
        "-F", f"state={payload['state']}",
        "-F", f"context={payload['context']}",
        "-F", f"description={payload['description']}",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=ROOT)
        if result.returncode == 0:
            return True, ""
        return (
            False,
            f"Failed to emit status check (code {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}",
        )
    except Exception as exc:
        return False, f"Error during status emission: {exc}"


def enqueue_status_check_outbox(
    task: dict[str, Any],
    payload: dict[str, str],
    error: str,
) -> None:
    """Durably retain one exact failed payload without duplicate queue growth."""
    outbox = task.setdefault("status_check_outbox", [])
    existing = next(
        (
            item
            for item in outbox
            if isinstance(item, dict)
            and all(
                str(item.get(key) or "") == str(payload.get(key) or "")
                for key in ("repo_slug", "sha", "state", "context", "description")
            )
        ),
        None,
    )
    now = iso_now()
    if existing is not None:
        existing["last_error"] = error
        existing["last_attempt_at"] = now
        existing["attempt_count"] = int(existing.get("attempt_count", 0) or 0) + 1
        return
    outbox.append(
        {
            **payload,
            "created_at": now,
            "last_attempt_at": now,
            "attempt_count": 1,
            "last_error": error,
        }
    )


def reconcile_status_check_outbox(state: dict[str, Any]) -> tuple[int, int]:
    """Retry exact failed status payloads and remove only confirmed deliveries."""
    delivered = 0
    retained = 0
    for task in state.get("tasks", []):
        pending = task.get("status_check_outbox")
        if not isinstance(pending, list) or not pending:
            continue
        remaining: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            payload = {
                key: str(item.get(key) or "")
                for key in ("repo_slug", "sha", "state", "context", "description")
            }
            if not all(payload.values()):
                item["last_error"] = "Malformed status-check outbox payload"
                remaining.append(item)
                retained += 1
                continue
            ok, error = post_task_review_status_payload(payload)
            if ok:
                delivered += 1
                task.setdefault("status_check_delivery_history", []).append(
                    {
                        **payload,
                        "created_at": item.get("created_at"),
                        "attempt_count": int(item.get("attempt_count", 0) or 0) + 1,
                        "delivered_at": iso_now(),
                    }
                )
                print(
                    f"Reconciled status check '{payload['context']}'="
                    f"{payload['state']} for {payload['sha'][:8]}.",
                    file=sys.stdout,
                )
                continue
            item["last_error"] = error
            item["last_attempt_at"] = iso_now()
            item["attempt_count"] = int(item.get("attempt_count", 0) or 0) + 1
            remaining.append(item)
            retained += 1
        if remaining:
            task["status_check_outbox"] = remaining
        else:
            task.pop("status_check_outbox", None)
    return delivered, retained


def review_gate_head_drifted(task: dict[str, Any]) -> bool:
    """True when the branch has advanced past the commit that carries the gate.

    Emission used to fire only on a status transition, and nothing else in the
    orchestrator ever posts this check. So a branch that moved after its last
    transition -- composing the base, or recording closeout evidence -- left the
    new head with no `task-review-gate` at all. Because it is a *required* check,
    absent reads as unmergeable, and no code path would ever put it back.

    Observed live on 2026-08-04: PRs #616 and #622 both carried four green checks
    and no gate, while #628, whose head had not moved since registration, carried
    all five.
    """

    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return False

    last = str(task.get("review_gate_sha") or "").strip()
    if not last:
        # Never emitted, or emitted before this field existed. A status transition
        # covers the first case; re-posting here would fire on every unrelated
        # sync for the second, so stay quiet and let the transition drive it.
        return False

    current = resolve_task_sha(task_id)
    return bool(current) and current != last


def emit_task_review_status_check(task: dict[str, Any], state_status: str) -> None:
    payload = task_review_status_payload(task, state_status)
    if payload is None:
        return
    ok, error = post_task_review_status_payload(payload)
    if ok:
        # Remember which commit carries the gate. A GitHub status belongs to one
        # SHA, so once the branch advances the new head has no gate at all and the
        # required check reads as absent rather than failing. Recording the SHA is
        # what lets the next sync notice the drift and re-post.
        task["review_gate_sha"] = payload.get("sha") or task.get("review_gate_sha")
        print(
            f"Successfully emitted status check '{payload['context']}'="
            f"{payload['state']} to GitHub API.",
            file=sys.stdout,
        )
        return
    print(error, file=sys.stderr)
    enqueue_status_check_outbox(task, payload, error)
    print(
        "Warning: Status check emission failed; exact payload recorded for reconciliation.",
        file=sys.stderr,
    )


def emit_status_checks_for_changed_tasks(state_before: dict[str, Any], state_after: dict[str, Any], command: str, args: list[str]) -> None:
    before_statuses = {t["id"]: t for t in state_before.get("tasks", []) if "id" in t}
    after_tasks = {t["id"]: t for t in state_after.get("tasks", []) if "id" in t}

    target_task_id = (
        args[0]
        if (
            args
            and command
            in {
                "approve",
                "reopen",
                "handoff",
                "progress",
                "start",
                "re_review",
                "re-review",
                "restore_approved",
                "restore_approved_head",
            }
        )
        else None
    )

    for task_id, after_task in after_tasks.items():
        before_task = before_statuses.get(task_id)
        before_status = before_task.get("status") if before_task else None
        after_status = after_task.get("status")

        is_target = target_task_id and (str(task_id).upper() == str(target_task_id).upper())
        if after_status != before_status or is_target or review_gate_head_drifted(after_task):
            emit_task_review_status_check(after_task, after_status)


READ_ONLY_COMMANDS = {
    "prompt": command_prompt,
    "show": command_show,
}

MUTATING_COMMANDS = {
    "assign": command_assign,
    "start": command_start,
    "progress": command_progress,
    "note": command_note,
    "reopen": command_reopen,
    "re_review": command_re_review,
    "re-review": command_re_review,
    "handoff": command_handoff,
    "blocker": command_blocker,
    "retarget_blocker": command_retarget_blocker,
    "prune_agents": command_prune_agents,
    "done": command_done,
    "restore_approved": command_restore_approved,
    "restore_approved_head": command_restore_approved_head,
    "supersede": command_supersede,
    "approve": command_approve,
    "archive_migrate": command_archive_migrate,
    "sync": command_sync,
    "wave": command_wave,
}

# The one mutating command that records nothing under an agent name: `sync`
# only recomputes derived views. Every other entry in MUTATING_COMMANDS must
# read its actor through current_actor_validated() before its first mutation.
# Anything added here is a deliberate, reviewed exemption, not an oversight —
# `ActorCommandMutationGuardTests` fails if a command escapes both sets.
ACTORLESS_MUTATING_COMMANDS = frozenset({"sync"})


def main(argv: list[str]) -> int:
    state = load_state()
    command = argv[1] if len(argv) > 1 else "sync"
    args = argv[2:]

    read_only_commands = READ_ONLY_COMMANDS
    commands = MUTATING_COMMANDS

    if command in read_only_commands:
        read_only_commands[command](state, args)
        return 0

    if command not in commands:
        raise SystemExit(f"Unknown command: {command}")

    state_before = deepcopy(state)
    commands[command](state, args)
    sync_all(state)
    try:
        reconcile_status_check_outbox(state)
        emit_status_checks_for_changed_tasks(state_before, state, command, args)
        sync_all(state)
    except Exception as exc:
        print(f"Warning: Failed to emit status checks: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
