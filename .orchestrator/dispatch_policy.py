from __future__ import annotations

from typing import Any

from common import normalize_agent_id
from provider_runtime import provider_config_entry

REASON_REVIEW_READY = "review_ready_dispatch"
REASON_OWNED_FINALIZE = "owned_finalize_dispatch"
REASON_OWNED_IN_PROGRESS = "owned_in_progress_dispatch"
REASON_OWNED_READY = "owned_ready_dispatch"
REASON_HELPER_CLAIM = "helper_claim_dispatch"

EXECUTION_DISPATCH_REASONS = {
    REASON_REVIEW_READY,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_HELPER_CLAIM,
}

DISPATCH_REASON_PRIORITIES = {
    REASON_REVIEW_READY: 0,
    REASON_OWNED_FINALIZE: 1,
    REASON_OWNED_IN_PROGRESS: 2,
    REASON_OWNED_READY: 3,
    REASON_HELPER_CLAIM: 4,
}

DISPATCH_STATUS_ACTIONS = {
    REASON_OWNED_READY: ("start", {"todo"}),
    REASON_OWNED_FINALIZE: ("note", {"review_approved"}),
    REASON_OWNED_IN_PROGRESS: ("progress", {"in_progress"}),
    REASON_HELPER_CLAIM: ("progress", {"todo", "in_progress"}),
}

DEFAULT_REVIEW_STATUSES = ["review"]
DEFAULT_FINALIZE_STATUSES = ["review_approved"]
DEFAULT_OWNED_STATUSES = ["in_progress", "todo"]
DEFAULT_SIDECAR_ONLY_AGENTS: list[str] = []
DEFAULT_DISABLED_AGENTS: list[str] = []
DEFAULT_DEPENDENCY_DONE_STATUSES = ["done"]
# An `in_progress` task whose owner has no live runner is orphaned work, not
# work in flight, so the helper lease has to be able to reach it. Named here so
# the dispatch loop can tell "the operator narrowed this" apart from "the
# operator never set it" -- `ready_dispatch_settings` applies it via
# `setdefault`, which an explicit runtime value silently wins over.
DEFAULT_HELPER_CLAIMABLE_STATUSES = ["todo", "in_progress"]
DEFAULT_WORKER_TERMINAL_STATUSES = ["review", "done", "review_approved"]
DEFAULT_ACTIVE_WORKER_STATUSES = [
    "running",
    "waiting_approval",
    "retry_backoff",
    "stalled",
]
DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS = 300
DEFAULT_WORKER_OS_DUPLICATE_GUARD = True
DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK = 1


#: Where the single role/task-class provider eligibility policy is configured.
#: It lives under `ready_dispatcher` next to `owner_provider_preference`
#: because the two answer adjacent halves of one question: this block decides
#: *which lanes are allowed at all*, and the preference block only orders the
#: lanes that survive it. Keeping them apart -- a hard gate and a soft rank --
#: is what stops the fleet from growing a second selector.
ROLE_PROVIDER_POLICY_KEY = "role_provider_policy"

ROLE_OWNER = "owner"
ROLE_REVIEWER = "reviewer"
ROLE_HELPER = "helper"
#: The roles a policy rule may name. An unknown role in config is a typo that
#: would silently widen eligibility, so it is rejected rather than ignored.
POLICY_ROLES = (ROLE_OWNER, ROLE_REVIEWER, ROLE_HELPER)

#: How a dispatch reason maps onto the role its target agent is about to play.
#: Finalize is owner work: it is the owner making its own approved head durable.
DISPATCH_REASON_ROLES = {
    REASON_REVIEW_READY: ROLE_REVIEWER,
    REASON_OWNED_FINALIZE: ROLE_OWNER,
    REASON_OWNED_IN_PROGRESS: ROLE_OWNER,
    REASON_OWNED_READY: ROLE_OWNER,
    REASON_HELPER_CLAIM: ROLE_HELPER,
}

#: Accepted values of `unclassified_owned_work`, plus the list form.
UNCLASSIFIED_ALLOW = "allow"
UNCLASSIFIED_DENY = "deny"

#: Statuses whose actors are frozen no matter how the fleet is configured.
#: Entering `review_approved` pins an exact reviewed PR head, and closeout from
#: there is read-only with respect to the branch.
DEFAULT_FROZEN_CLOSEOUT_STATUSES = ("review_approved",)


def task_closeout_is_frozen(config: dict[str, Any], task: dict[str, Any] | None) -> bool:
    """Whether an approved or merging head has already fixed this task's actors.

    `review_approved` freezes the exact reviewed PR head, and `approved_head` /
    `merge_route` record that the branch is being composed into its base. The
    actors of such a task are not an open question about who should do the work;
    they name whoever already did it, and the owner must finalize its own
    approved commit. Read through the configured `finalize_statuses` so a fleet
    that spells the state differently is covered, with `review_approved` always
    included because the freeze is a property of the transition rather than of
    the spelling.
    """
    if not isinstance(task, dict):
        return False
    if task.get("approved_head") or task.get("merge_route") is not None:
        return True
    task_status = str(task.get("status") or "").strip().lower()
    if not task_status:
        return False
    configured = ready_dispatch_settings(config).get("finalize_statuses")
    if isinstance(configured, str):
        configured = [configured]
    frozen = {str(value).strip().lower() for value in list(configured or []) if str(value).strip()}
    frozen.update(DEFAULT_FROZEN_CLOSEOUT_STATUSES)
    return task_status in frozen


def dispatch_reason_role(reason: str | None) -> str | None:
    """The role an agent plays when it receives this dispatch reason."""
    return DISPATCH_REASON_ROLES.get(str(reason or ""))


def agent_provider_identity_ids(config: dict[str, Any], agent_name: str | None) -> set[str]:
    """Every configured provider/adapter id that names the model behind an agent.

    A display name is not model identity. `Antigravity2` runs on the
    `antigravity2` provider alias, whose `delivery_mode` and whose agent
    `adapter` are both `antigravity`; guessing from the name would either miss
    that alias or start matching on spelling. Resolving through the configured
    provider entry keeps provider policy a statement about providers.

    This is the leaf implementation. `worker_failure_policy` re-exports it for
    the supervisor scope and `scripts/ai_status.py` imports it directly, so the
    dispatcher and the canonical CLI resolve provider identity the same way
    without the CLI having to import the supervisor.
    """
    agent_id = normalize_agent_id(agent_name or "")
    if not agent_id:
        return set()
    agent = (config.get("agents", {}) or {}).get(agent_id, {}) or {}
    provider_id = str(agent.get("provider") or agent_id)
    canonical_key, provider_cfg = provider_config_entry(config, provider_id)
    identities = {
        normalize_agent_id(provider_id),
        normalize_agent_id(str(canonical_key or "")),
        normalize_agent_id(str(agent.get("adapter") or "")),
        normalize_agent_id(str(provider_cfg.get("delivery_mode") or "")),
        normalize_agent_id(str(provider_cfg.get("adapter") or provider_cfg.get("type") or "")),
    }
    identities.discard("")
    return identities


def known_provider_identity_ids(config: dict[str, Any]) -> set[str]:
    """Provider ids a policy may legitimately name in this configuration.

    A policy entry naming a provider this fleet does not have is not a harmless
    no-op: written into a `providers` list it silently shrinks the allowed set,
    and written as the only entry it silently empties it. Validating against the
    ids that actually resolve is what turns that into a visible config error.
    """
    known: set[str] = set()
    for provider_id, provider_cfg in (config.get("providers", {}) or {}).items():
        known.add(normalize_agent_id(str(provider_id)))
        if not isinstance(provider_cfg, dict):
            continue
        known.add(normalize_agent_id(str(provider_cfg.get("delivery_mode") or "")))
        known.add(normalize_agent_id(str(provider_cfg.get("adapter") or provider_cfg.get("type") or "")))
    for agent_id, agent in (config.get("agents", {}) or {}).items():
        if not isinstance(agent, dict):
            continue
        known |= agent_provider_identity_ids(config, str(agent_id))
    known.discard("")
    return known


def _normalized_id_list(values: Any) -> list[str] | None:
    """Normalize a policy string-list field, or None when it is malformed."""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return None
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized.append(normalize_agent_id(value))
    return normalized


def role_provider_policy_settings(config: dict[str, Any]) -> dict[str, Any] | None:
    """The configured role/task-class provider policy, or None when unset.

    None means "no policy", which is deliberately distinct from an empty or a
    disabled one: a fleet that has never configured this block keeps exactly the
    previous eligibility behaviour, and nothing on this path runs for it.
    """
    settings = ready_dispatch_settings(config).get(ROLE_PROVIDER_POLICY_KEY)
    if not isinstance(settings, dict) or not settings:
        return None
    if settings.get("enabled") is False:
        return None
    return settings


def role_provider_policy_error(config: dict[str, Any]) -> str | None:
    """Describe why an enabled policy cannot be applied, or None when it is sound.

    An enabled-but-unreadable policy is the dangerous case: the rule that says
    "only Codex reviews" is exactly the rule a typo would delete, and a deleted
    rule looks identical to no rule at all. So a malformed policy denies rather
    than degrades, and says which key it could not read.
    """
    settings = role_provider_policy_settings(config)
    if settings is None:
        return None
    known_providers = known_provider_identity_ids(config)

    def provider_list_error(values: Any, where: str) -> str | None:
        providers = _normalized_id_list(values)
        if providers is None:
            return f"{where} must be a list of non-empty provider id strings"
        if not providers:
            return f"{where} must name at least one provider"
        unknown = sorted(set(providers) - known_providers)
        if unknown:
            return f"{where} names provider(s) not configured in this fleet: {', '.join(unknown)}"
        return None

    rules = settings.get("rules")
    if not isinstance(rules, list):
        return "rules must be a list"
    for index, rule in enumerate(rules):
        where = f"rules[{index}]"
        if not isinstance(rule, dict):
            return f"{where} must be an object"
        roles = _normalized_id_list(rule.get("roles"))
        if not roles:
            return f"{where}.roles must be a list of non-empty role names"
        unknown_roles = sorted(set(roles) - set(POLICY_ROLES))
        if unknown_roles:
            return f"{where}.roles names unknown role(s): {', '.join(unknown_roles)}"
        if "task_classes" in rule:
            task_classes = _normalized_id_list(rule.get("task_classes"))
            if not task_classes:
                return f"{where}.task_classes must be a list of non-empty task_class names"
        error = provider_list_error(rule.get("providers"), f"{where}.providers")
        if error:
            return error

    unclassified = settings.get("unclassified_owned_work")
    if unclassified is None:
        # Owned work with no `task_class` is the gap every class-scoped rule
        # leaves behind. Leaving it implicit is how such work would quietly keep
        # falling back to whichever lane sorts first, so an enabled policy has to
        # state what happens to it.
        return "unclassified_owned_work must be set to \"allow\", \"deny\", or a provider list"
    if isinstance(unclassified, str):
        if normalize_agent_id(unclassified) not in {UNCLASSIFIED_ALLOW, UNCLASSIFIED_DENY}:
            return "unclassified_owned_work string must be \"allow\" or \"deny\""
    else:
        error = provider_list_error(unclassified, "unclassified_owned_work")
        if error:
            return error
    return None


def allowed_role_provider_ids(
    config: dict[str, Any],
    *,
    role: str | None,
    task_class: str | None,
) -> set[str] | None:
    """Provider ids allowed to hold `role` on a task of `task_class`.

    None means the policy does not restrict this combination, which is what
    every caller sees when no policy is configured. An empty set means the
    policy restricts it to nothing -- nobody may take it -- and is not the same
    answer.

    `task_class` distinguishes three cases on purpose. A concrete value matches
    class-scoped rules; `""` means "this task has no class", which is what
    `unclassified_owned_work` governs; `None` means "the caller cannot show a
    task at all", where only role-wide rules can be applied and the unclassified
    disposition must not be guessed at.
    """
    settings = role_provider_policy_settings(config)
    if settings is None:
        return None
    normalized_role = normalize_agent_id(role or "")
    if normalized_role not in POLICY_ROLES:
        return None
    normalized_class = normalize_agent_id(task_class or "") if task_class is not None else None

    allowed: set[str] | None = None
    matched_class_rule = False
    for rule in settings.get("rules") or []:
        roles = _normalized_id_list(rule.get("roles")) or []
        if normalized_role not in roles:
            continue
        if "task_classes" in rule:
            if not normalized_class:
                continue
            if normalized_class not in (_normalized_id_list(rule.get("task_classes")) or []):
                continue
            matched_class_rule = True
        providers = set(_normalized_id_list(rule.get("providers")) or [])
        # Two rules reaching the same (role, task_class) both constrain it, so
        # the allowed set is their intersection: a lane must satisfy every rule
        # that speaks about it, not merely one of them.
        allowed = providers if allowed is None else (allowed & providers)

    if (
        allowed is None
        and not matched_class_rule
        and normalized_class == ""
        and normalized_role in {ROLE_OWNER, ROLE_HELPER}
    ):
        unclassified = settings.get("unclassified_owned_work")
        if isinstance(unclassified, str):
            if normalize_agent_id(unclassified) == UNCLASSIFIED_DENY:
                return set()
            return None
        return set(_normalized_id_list(unclassified) or [])
    return allowed


def role_provider_block_reason(
    config: dict[str, Any],
    agent_name: str | None,
    *,
    role: str | None,
    task_class: str | None = None,
    task: dict[str, Any] | None = None,
) -> str | None:
    """Why this agent may not hold `role` here, or None when the policy allows it.

    This is the single eligibility predicate. Initial assignment, repair and
    reconcile, reviewer failover, owner-failure fallback, review churn rotation,
    helper claims, the queued pre-launch recheck and the canonical CLI all reach
    the policy through this one function, so none of them can drift into a
    private notion of which provider may do what.

    Pass `task` when there is one; `task_class` is then read from it, and an
    explicit `task_class` still wins for callers that know the class an
    assignment is about to acquire.
    """
    if role_provider_policy_settings(config) is None:
        return None
    error = role_provider_policy_error(config)
    if error:
        return f"role_provider_policy is enabled but malformed ({error}); dispatch fails closed"
    # A frozen closeout is outside the policy entirely, for both actors. Here the
    # policy would not be choosing who should do the work -- the work is done and
    # its head is pinned. Applying it would evict the owner from finalizing its
    # own approved commit, or rewrite which reviewer approved it, which is the
    # one thing a change of policy must never do retroactively.
    if task_closeout_is_frozen(config, task):
        return None
    if task_class is None and isinstance(task, dict):
        task_class = str(task.get("task_class") or "")
    allowed = allowed_role_provider_ids(config, role=role, task_class=task_class)
    if allowed is None:
        return None
    name = str(agent_name or "").strip()
    # Resolve identity from the name as given. A display placeholder must never
    # reach the resolver: `normalize_agent_id` would turn it into a perfectly
    # ordinary-looking id, and an agent with no identity would stop failing
    # closed because it had acquired a fabricated one.
    identities = agent_provider_identity_ids(config, name)
    label = name or "(unnamed agent)"
    scope = f"role {normalize_agent_id(role or '')}"
    if task_class:
        scope = f"{scope} on task_class {normalize_agent_id(task_class)}"
    elif task_class == "":
        scope = f"{scope} on unclassified work"
    if not allowed:
        return f"role_provider_policy permits no provider for {scope}"
    if not identities:
        # An agent whose provider cannot be resolved is an unknown provider, and
        # an unknown provider is not evidence of permission.
        return f"{label} has no resolvable provider identity, so {scope} fails closed"
    if not identities & allowed:
        return (
            f"{label} provider {', '.join(sorted(identities))} is not permitted for "
            f"{scope} (allowed: {', '.join(sorted(allowed))})"
        )
    return None


def dispatch_reason_priority(reason: str | None) -> int | None:
    return DISPATCH_REASON_PRIORITIES.get(str(reason or ""))


def is_execution_dispatch_reason(reason: str | None) -> bool:
    return str(reason or "") in EXECUTION_DISPATCH_REASONS


def task_priority_rank(task: dict[str, Any] | None) -> int:
    """Return the durable business priority rank used by the ready dispatcher.

    Lifecycle work (review/finalize/execute) remains a tie-breaker, not the
    primary priority.  Older state files often omit a priority, so an unset or
    malformed value deliberately sorts after P0-P3 instead of silently being
    treated as P0.
    """
    value = str((task or {}).get("priority") or "").strip().upper()
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(value, 4)


def normalized_status_set(values: Any, default: list[str]) -> set[str]:
    if values is None:
        values = default
    if isinstance(values, str):
        values = [values]
    return {str(value).lower() for value in list(values or [])}


def ready_dispatch_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(config.get("ready_dispatcher", {}) or {})
    settings.setdefault("enabled", True)
    settings.setdefault("review_statuses", list(DEFAULT_REVIEW_STATUSES))
    settings.setdefault("finalize_statuses", list(DEFAULT_FINALIZE_STATUSES))
    settings.setdefault("owned_statuses", list(DEFAULT_OWNED_STATUSES))
    settings.setdefault("sidecar_only_agents", list(DEFAULT_SIDECAR_ONLY_AGENTS))
    settings.setdefault("disabled_agents", list(DEFAULT_DISABLED_AGENTS))
    legacy_done_statuses = settings.get("done_statuses", list(DEFAULT_WORKER_TERMINAL_STATUSES))
    settings.setdefault("dependency_done_statuses", list(DEFAULT_DEPENDENCY_DONE_STATUSES))
    settings.setdefault("worker_terminal_statuses", legacy_done_statuses)
    settings.setdefault("active_worker_statuses", list(DEFAULT_ACTIVE_WORKER_STATUSES))
    settings.setdefault("orphaned_queue_event_grace_seconds", DEFAULT_ORPHANED_QUEUE_EVENT_GRACE_SECONDS)
    settings.setdefault("worker_os_duplicate_guard", DEFAULT_WORKER_OS_DUPLICATE_GUARD)
    settings.setdefault("max_active_workers_per_task", DEFAULT_MAX_ACTIVE_WORKERS_PER_TASK)
    helper = dict(settings.get("helper_execution_lease", {}) or {})
    helper.setdefault("enabled", True)
    helper.setdefault("claimable_statuses", list(DEFAULT_HELPER_CLAIMABLE_STATUSES))
    helper.setdefault("dispatch_sla_seconds", 600)
    helper.setdefault("lease_seconds", 1800)
    helper.setdefault("max_claims_per_tick", 4)
    helper.setdefault("max_claims_per_agent", 2)
    helper.setdefault("require_owner_saturated", True)
    settings["helper_execution_lease"] = helper
    return settings


def worker_logical_dispatch_agent_id(config: dict[str, Any], worker: dict[str, Any]) -> str:
    """Resolve the logical agent a worker's dispatch slot belongs to.

    Lives here rather than in `dispatch_engine` because it is the one name that
    made the module graph cyclic: `dispatch_engine` needs seven names from
    `worker_failure_policy`, and `worker_failure_policy` needed only this one
    back. It is a pure function of `config` and the worker record -- no dispatch
    state -- so a leaf is where it belongs.
    """
    explicit = normalize_agent_id(str(worker.get("logical_agent_id") or ""))
    if explicit:
        return explicit
    agent_id = normalize_agent_id(str(worker.get("agent_id") or worker.get("provider") or ""))
    agent = config.get("agents", {}).get(agent_id, {}) or {}
    return normalize_agent_id(str(agent.get("dispatch_slot_for") or agent_id))
