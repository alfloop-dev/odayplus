from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

from common import load_status, normalize_agent_id
from multi_repo_registry import (
    checkout_origin_endpoint,
    registered_remote_endpoint,
    repository_local_path,
    repository_slug,
    resolve_repository,
    task_repository_id,
)
from provider_runtime import provider_config, provider_config_entry
from task_archive import TaskResolver

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

#: The inverse of `DISPATCH_REASON_PRIORITIES`. A lane priority is what the
#: eligibility predicate answers with, but role/provider policy is asked about a
#: reason, so the two have to be convertible without a second table restating
#: the mapping. Derived rather than written out for exactly that reason.
DISPATCH_PRIORITY_REASONS = {
    priority: reason for reason, priority in DISPATCH_REASON_PRIORITIES.items()
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


#: Statuses in which the owner field has stopped being a question about who
#: should build something and has become a claim about who already did.
DEFAULT_SUBMITTED_AUTHOR_STATUSES = ("review", "review_approved")

_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def is_human_gate_agent(agent_name: str | None) -> bool:
    """Whether a name is a human gate rather than an automated account.

    The leaf implementation; `worker_failure_policy` imports it for the
    supervisor scope, so the source rule below and every selector agree on
    which names have no account pool by design.
    """
    name = str(agent_name or "").strip().casefold()
    if not name:
        return False
    return name in {"human/ops", "human", "ops"} or name.startswith("human/")


class ReviewSourceIdentity(NamedTuple):
    """Who a review of this task has to be independent of.

    `submitted` says whether already-submitted work fixes these actors at all.
    `submitter` is the genuine `submitted_by` -- the finalizer, which is not
    necessarily anyone who wrote a commit -- and is what pins the owner field.
    `source_authors` are the immutable `LLM-Agent` actors of the submitted
    range exactly as recorded. `agents` and `pools` are every one of those,
    plus the submitter and the current owner, resolved through the registered
    agent/account-pool configuration. `error` is set when any of that cannot be
    established, and then no reviewer is independent.
    """

    submitted: bool = False
    submitter: str = ""
    source_authors: tuple[str, ...] = ()
    agents: frozenset[str] = frozenset()
    pools: frozenset[str] = frozenset()
    error: str | None = None

    def pins_owner(self, owner: str | None) -> bool:
        """Whether the owner field is the finalizer of submitted work.

        The owner that submitted -- or a submitted record that does not say who
        did -- is not a slot the reconciler may re-fill: replacing it would
        relabel the finalizer of a pinned head, and a missing submitter is not
        evidence that someone else did it.
        """
        if not self.submitted:
            return False
        return not self.submitter or normalize_agent_id(self.submitter) == normalize_agent_id(owner or "")


def _registered_actor_id(config: dict[str, Any], name: str) -> str:
    """The configured agent id a recorded actor name refers to, or empty.

    A name is registered when it is an agent id, the configured display name of
    a logical (non-slot) agent, or both -- provided every one of those matches
    is the same agent. Adapters record `LLM-Agent` as the display name, so an
    id match does not outrank a display-name match: `Codex` naming agent
    `codex` by id and agent `writer` by display name is two actors, and picking
    either would drop the other's pool. Such a conflict, and anything else --
    including a label that merely resembles a registered one -- is unknown, not
    guessed.
    """
    agents = config.get("agents", {}) or {}
    wanted = str(name or "").strip().casefold()
    if not wanted:
        return ""
    agent_id = normalize_agent_id(name)
    matches = {agent_id} if agent_id and isinstance(agents.get(agent_id), dict) else set()
    matches.update(
        normalize_agent_id(str(key))
        for key, agent in agents.items()
        if isinstance(agent, dict)
        and not str(agent.get("dispatch_slot_for") or agent.get("dispatch_slot_for_pool") or "").strip()
        and str(agent.get("display_name") or agent.get("name") or "").strip().casefold() == wanted
    )
    return next(iter(matches)) if len(matches) == 1 else ""


def _registered_actor_pool(config: dict[str, Any], name: str) -> str:
    """The account pool of a registered agent, or empty for an unknown name.

    `agent_account_pool_id` answers for any string -- an unregistered label
    falls through to a provider group spelled like the label itself, which is
    always "independent" of every real pool. Registration is therefore checked
    first, so an unknown actor is unresolvable rather than conveniently foreign.
    """
    agent_id = _registered_actor_id(config, name)
    return agent_account_pool_id(config, agent_id) if agent_id else ""


def delivery_merge_target_branch(config: dict[str, Any], repository_id: str) -> str:
    if repository_id == "pantheon":
        branch = str((config.get("branch_workflow") or {}).get("dev_branch") or "").strip()
        if branch:
            return branch
    repo = resolve_repository(config, repository_id)
    branch = str(repo.get("default_branch") or "").strip()
    return branch or "main"


_UNUSABLE_BRANCH_CHARS = re.compile(r"[\s~^:?*\[\\]")


def task_explicit_branch(
    task: dict[str, Any] | None = None,
    branch: str | None = None,
) -> str | None:
    """The branch explicitly recorded on the task, or None if absent/invalid.

    Reuses the validity rules from ``task_branch_name`` (rejecting whitespace,
    git ref metacharacters, and empty strings) without falling back to the
    derived ``task/<id>`` default name.
    """
    candidate = branch if branch is not None else (task or {}).get("branch")
    recorded = str(candidate or "").strip()
    if recorded and not _UNUSABLE_BRANCH_CHARS.search(recorded) and ".." not in recorded:
        return recorded
    return None


def task_branch_name(task: dict[str, Any] | None, task_id: str | None = None) -> str:
    """The task's branch as recorded, falling back to the derived name.

    Building `task/<id>` unconditionally invented a branch for every task
    reimported from an existing GitHub PR, whose branch does not follow that
    convention. Looking a task up by an invented ref finds nothing, and the
    caller reads that as missing work rather than as a wrong question.
    """
    explicit = task_explicit_branch(task)
    if explicit:
        return explicit
    resolved_id = str(task_id or (task or {}).get("id") or "").strip()
    return f"task/{resolved_id}"


def _git_succeeds(repo_root: Path, *args: str) -> bool:
    try:
        proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, check=False)
    except OSError:
        return False
    return proc.returncode == 0


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _is_integration_point(repo_root: Path, commit: str, target_ref: str) -> bool:
    """Whether `commit` was itself a tip of `target_ref`, not merely merged into it.

    Once a branch is merged, every one of its commits is in the target's
    history, so ancestry alone would accept a commit from the middle of the
    delivered range as its base. The integration line is the target's
    first-parent chain: walking it back from the tip, stopping at whatever
    `commit` already contains, must end exactly on `commit`.
    """
    chain = _git_output(repo_root, "rev-list", "--first-parent", "--parents", target_ref, "--not", commit)
    if chain is None:
        return False
    if not chain:
        return _git_output(repo_root, "rev-parse", "--verify", "--quiet", f"{target_ref}^{{commit}}") == commit
    last = chain.splitlines()[-1].split()
    return len(last) > 1 and last[1].lower() == commit


def _delivery_toolchain() -> Any:
    """The delivery-identity scanner module `submit_review` validates a range with."""
    git_dir = Path(__file__).resolve().parents[1] / "delivery_toolchain" / "git"
    if str(git_dir) not in sys.path:
        sys.path.insert(0, str(git_dir))
    import check_task_delivery_identity

    return check_task_delivery_identity


def _inspect_delivery_identity(repo_root: Path, **kwargs: Any) -> Any:
    """The delivery-identity scanner `submit_review` validates a range with."""
    return _delivery_toolchain().inspect_delivery_identity(repo_root, **kwargs)


def task_unresolved_human_or_independent_gate_reason(task: dict[str, Any]) -> str | None:
    """Return why a task carries an unresolved human or independent gate blocking submission."""
    if not isinstance(task, dict):
        return None
    task_class = str(task.get("task_class") or "").strip().lower()
    if task_class == "human_gate" or bool(task.get("non_dispatchable")):
        return "task carries an unresolved human gate (human or non-dispatchable gate)"
    if task.get("requires_human_approval") is True or task.get("human_required_roles"):
        return "task carries an unresolved human approval gate"
    for gate_field in (
        "credentials_gate",
        "credential_gate",
        "deployment_gate",
        "production_gate",
        "external_data_gate",
        "human_gate",
    ):
        if task.get(gate_field):
            return f"task carries an unresolved independent {gate_field}"
    gate_status = str(task.get("gate_status") or "").strip().casefold()
    if gate_status.startswith("pending_human"):
        return f"task carries an unresolved human gate status ({gate_status})"
    return None


#: The revision every canonical board write stamps (`status_transition`): a
#: board without it is not a versioned snapshot and proves nothing absent.
STATUS_WRITE_REVISION_FIELD = "_status_write_revision"


# Canonical blocker prose (casefolded). A path or filename may name the
# required deliverable itself (`waiting for dataset.csv`), so path syntax is
# never release authority. Only two bounded reference grammars prove a path is
# an incidental code pointer: a whole `refs=`/`ref:`/`see` clause, and the
# location of a failure (`provider failure in scripts/deployment.py`), each
# ending at a clause boundary.
_CODE_REFERENCE_PATH = (
    r"`?(?:(?:~|\.{1,2})?/[a-z0-9_.\-/]*[a-z0-9_\-]"
    r"|[a-z0-9_.\-]+/[a-z0-9_.\-/]*[a-z0-9_\-]"
    r"|[a-z0-9_.\-/]*[a-z0-9_\-]\.[a-z0-9]{1,8})`?"
)
_CODE_REFERENCE_CLAUSE_END = r"\s*(?=[;；\n]|$)"
_CODE_REFERENCE_LABEL_CLAUSE_RE = re.compile(
    # A label must end at a separator: `seed/dataset` and `reference.json`
    # are artifacts, not `see`/`ref` clauses with an empty delimiter.
    r"(?:^|(?<=[;；\n]))\s*(?:refs?|see)(?:\s*[:=]\s*|\s+)"
    + _CODE_REFERENCE_PATH
    + r"(?:\s*,\s*" + _CODE_REFERENCE_PATH + r")*"
    + _CODE_REFERENCE_CLAUSE_END
)
_CODE_REFERENCE_FAILURE_LOCATION_RE = re.compile(
    r"\b(failure|failures|failed|error|errors|crash|crashed|exception|traceback|timeout)"
    r"\s+(?:in|at)\s+" + _CODE_REFERENCE_PATH + _CODE_REFERENCE_CLAUSE_END
)
# Only a complete routing-failure clause proves a slash compound describes
# scheduler infrastructure rather than an awaited artifact. Anchor both ends:
# `provider quota/worktree failure` is recoverable; `waiting for
# provider/worktree` and `required provider/worktree failure` are not.
_CANONICAL_ROUTING_FAILURE_CLAUSE_RE = re.compile(
    r"(?:^|(?<=[;；\n]))\s*(?:stale\s+)?(?:provider\s+)?"
    r"(?:provider|quota|worktree)(?:/(?:provider|quota|worktree))+\s+"
    r"(?:failure|failures|failed|error|errors|crash|crashed|timeout)"
    + _CODE_REFERENCE_CLAUSE_END
)
def _strip_explicit_code_reference_clauses(context: str) -> str:
    context = _CODE_REFERENCE_LABEL_CLAUSE_RE.sub(" ", context)
    return _CODE_REFERENCE_FAILURE_LOCATION_RE.sub(lambda match: f"{match.group(1)} ", context)


def blocked_task_prose_context(
    task: dict[str, Any], *, canonical_gate_prose: bool = False,
) -> str:
    """Return prose without task/dependency identifiers and code references.

    Canonical blocker prose is gate evidence, unlike an ordinary task note.
    A bare slash compound (External-data/dataset, Human/Ops) is ambiguous:
    do not erase it as a filesystem path. Only explicit paths/code references
    may be removed there; ambiguous gate evidence must fail closed.

    The gate keywords below are matched as substrings, so any task ID that
    happens to contain one poisons every task that depends on it: a task
    waiting on ``DPF-KRN-DATASET-001`` reads as an external-data gate purely
    because its dependency list mentions "dataset". Dependency identity is
    already carried structurally in ``depends_on``; strip those tokens before
    keyword matching so only genuine prose is classified.
    """
    identifiers = [str(task.get("id") or "")]
    identifiers.extend(str(dep) for dep in (task.get("depends_on") or []))
    for agent_key in ("owner", "reviewer", "waiting_for"):
        agent_val = str(task.get(agent_key) or "").strip()
        if agent_val and agent_val.casefold() not in {"human", "human/ops", "ops"}:
            identifiers.append(agent_val)
    raw_context = " ".join(
        str(task.get(key) or "")
        for key in (
            "next",
            "waiting_for",
            "blocker",
            "blocked_by",
            "failure_reason",
            "last_failure_reason",
            "push_status",
        )
    )

    # Mask only complete structural identity tokens, never substrings in
    # requirements (`Pi` inside `API`) or artifact/path components. Include
    # path/identifier punctuation in the boundary so `Pi.json`, `src/Pi` and
    # longer task IDs stay intact for fail-closed gate classification.
    context = raw_context.casefold()
    for identifier in identifiers:
        token = identifier.strip().casefold()
        if token:
            context = re.sub(
                r"(?<![\w./~+\-])" + re.escape(token) + r"(?![\w./~+\-])",
                " ",
                context,
            )

    # 1. Strip code blocks and inline backticks. In canonical blocker prose,
    # Markdown quoting is not proof of code: `human approval`, `pending_human`
    # or `docs/approval.json` may be the gate itself. Remove only the bounded
    # explicit reference clauses, then unwrap every remaining span so
    # hard/unknown/artifact classification still sees its content.
    if canonical_gate_prose:
        context = _strip_explicit_code_reference_clauses(context)
        context = re.sub(
            r"```([\s\S]*?)```|`([^`]*)`",
            lambda match: f" {match.group(1) if match.group(1) is not None else match.group(2)} ",
            context,
        )
    else:
        context = re.sub(r"```[\s\S]*?```", " ", context)
        context = re.sub(r"`[^`]*`", " ", context)

    # 2. Strip <key>=<value> pairs. Canonical blocker prose keeps both sides
    # as words: `approval=pending` is gate evidence, not a code reference.
    if canonical_gate_prose:
        context = re.sub(r"\s*=\s*", " ", context)
    else:
        context = re.sub(r"[A-Za-z0-9_.\-/]+\s*=\s*[A-Za-z0-9_.\-/]+", " ", context)

    # 3. Strip filesystem paths and file references from ordinary notes only.
    # In canonical blocker prose a path or filename may be the missing
    # deliverable; it survives and fails closed unless step 1 proved it an
    # explicit reference clause.
    if not canonical_gate_prose:
        context = re.sub(r"(?<!\S)/(?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]*[A-Za-z0-9_\-]+", " ", context)
        context = re.sub(r"(?:\./|\.\./)[A-Za-z0-9_.\-/]*[A-Za-z0-9_\-]+", " ", context)
        context = re.sub(
            r"\b[A-Za-z0-9_.\-/]*[A-Za-z0-9_\-]+\.(?:py|sh|tf|yml|yaml|json|toml|md|csv|txt|log|lock|ini|cfg)\b",
            " ",
            context,
            flags=re.IGNORECASE,
        )
        context = re.sub(r"\b[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-/]*[A-Za-z0-9_\-]+\b", " ", context)

    # 4. Strip snake_case identifiers containing underscore. In canonical
    # blocker prose a snake_case label (pending_human) may be the gate itself;
    # split it into words so hard/unknown classification still sees it.
    # Explicit code references were already removed above (backticks, paths).
    if canonical_gate_prose:
        context = context.replace("_", " ")
    else:
        context = re.sub(r"\b[A-Za-z0-9_]*_[A-Za-z0-9_]*\b", " ", context)

    # 5. Strip job references (e.g. 'deploy 相關 job', 'build job', 'deploy job')
    # from ordinary notes only. In canonical blocker prose an unfinished job
    # ('deployment job pending') is the gate itself, not a code reference.
    if not canonical_gate_prose:
        context = re.sub(
            r"\b[A-Za-z0-9_.\-]+\s*(?:相關\s*)?job\b", " ", context, flags=re.IGNORECASE
        )

    if canonical_gate_prose:
        context = _CANONICAL_ROUTING_FAILURE_CLAUSE_RE.sub(
            lambda match: match.group(0).replace("/", " "), context,
        )

    return context


_BLOCKER_RECOVERY_HARD_GATE_MARKERS = (
    "human/ops", "human gate", "pending_human", "pending human", "authoritative",
    "dataset", "attestation", "external-data", "external data", "mlflow",
    "deploy dev", "live-e2e", "production alias", "merge queue",
    "operator intervention", "manual approval", "requires operator",
    "human approval", "awaiting human", "pending approval", "approval pending",
    "awaiting approval", "approval required", "approval gate",
    "deployment", "production", "sign-off", "signoff",
    "credentials gate", "credential gate", "cross repo delivery",
    "business gate", "operator approval", "operator sign-off",
    "human sign-off", "human signoff", "manual sign-off", "manual signoff",
)

_BLOCKER_RECOVERY_HARD_GATE_WORDS_RE = re.compile(
    r"\b(human|operator|approval|signoff|sign-off|missing|unclassified|business|consent|client|external|dataset|live|raw|masked|manual|attestation|mlflow|deploy|deployment|production)\b",
    re.IGNORECASE,
)

# Every remaining slash is ambiguous artifact evidence, including extensionless
# relative paths whose components happen to be allowed routing words. Never
# split these into individually allowlisted tokens. Dotted filenames likewise
# remain unproven deliverables after explicit references have been removed.
_CANONICAL_ARTIFACT_RE = re.compile(r"/|\w\.\w")

_BLOCKER_RECOVERY_AGENT_NAMES = {
    "claude", "codex", "pi", "gemini", "copilot", "antigravity", "openai", "anthropic",
}

_BLOCKER_RECOVERY_ALLOWED_TOKENS = {
    # Provider & Agent terms
    "provider", "providers", "agent", "agents", "worker", "workers", "model", "models",
    "ai", "llm", "claude", "codex", "pi", "gemini", "copilot", "antigravity", "openai", "anthropic",
    # Routing, Handoff & Dispatch terms
    "route", "routes", "routed", "routing", "reroute", "rerouted", "rerouting",
    "reassign", "reassigned", "reassigning", "reassignment", "reassignments",
    "auto-reassigned", "auto-reassign", "auto", "sidecar", "sidecar-only", "mainline",
    "guard", "guards", "dispatch", "dispatched", "dispatching", "dispatcher",
    "dispatchable", "non-dispatchable", "handoff", "handoffs", "stale",
    "lane", "lanes", "away", "unavailable", "owner", "owners", "ownership",
    "reviewer", "reviewers", "review", "reviews", "reopen", "reopens", "reopened", "reopening",
    "repeated", "automatic",
    # Failure, Quota, Auth, Worktree terms
    "quota", "quotas", "exhausted", "exceeded", "rate-limit", "rate-limited",
    "ratelimit", "ratelimited", "rate", "limit", "limits", "limited", "limiting",
    "auth", "auths", "authn", "authz", "authenticated", "authentication",
    "credential", "credentials", "worktree", "worktrees", "workspace", "workspaces",
    "lock", "locks", "locked", "locking", "lease", "leases", "leased",
    "push", "pushed", "pushing", "push-failure", "fail", "fails", "failed", "failure",
    "failures", "error", "errors", "timeout", "timeouts", "timed-out", "timed_out",
    "timed", "out", "retry", "retrying", "retries", "recovery", "recover",
    "recovering", "recovered", "recovers", "resume", "resuming", "resumed", "resumes",
    "restart", "restarting", "restarted", "restarts", "disconnect", "disconnected",
    "disconnecting", "connection", "crash", "crashed", "jam", "jammed", "stuck",
    # Execution & Status terms
    "process", "processes", "execution", "runtime", "environment", "boundary",
    "interrupted", "terminated", "killed", "transient", "metadata", "correction",
    "notification", "notifications", "note", "notes", "notice", "status", "task",
    "tasks", "state", "code", "exit", "ordinary", "corrected", "unchanged",
    # Code-reference labels whose explicit path value was already removed
    "ref", "refs", "see",
    # Waiting/status terms, not prerequisites. Dependency words are removed
    # only in the resolver-authorized branch, never accepted as routing noise.
    "waiting", "waits", "waited", "wait",
    "completed", "satisfied", "resolved", "resolution", "release", "released", "releasing",
    # Glue / Syntactic terms
    "for", "to", "in", "on", "at", "of", "from", "with", "by", "about", "into",
    "through", "during", "before", "after", "above", "below", "between", "under", "over",
    "up", "down", "and", "or", "not", "no", "nor", "but", "yet", "so", "a", "an", "the",
    "this", "that", "these", "those", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "due", "only", "pending",
    "needed", "required", "again", "now", "then", "here", "there", "all", "any", "both",
    "each", "few", "more", "most", "other", "some", "such", "than", "too", "very",
    "can", "will", "just", "should", "may", "might",
}


# A colon or kind label is not prerequisite-release authority. Recognize the
# same dependency vocabulary in canonical blockers and retained task prose;
# only a nonempty, resolver-satisfied depends_on can authorize its removal.
_BLOCKER_RECOVERY_DEPENDENCY_WORDS_RE = re.compile(
    r"\b(?:depend|depends|depended|dependency|dependencies|dependent|upstream|prerequisites?)\b"
)
_BLOCKER_RECOVERY_DEPENDENCY_PROSE_RE = re.compile(
    r"\b(?:waiting\s+for\s+)?(?:depend|depends|depended|dependency|dependencies|dependent|upstream|prerequisites?)\b"
    r"(?:\s+gate\b)?\s*:?"
)


def _token_is_allowed(part: str) -> bool:
    if part in _BLOCKER_RECOVERY_ALLOWED_TOKENS:
        return True
    if part.isdigit():
        return True
    if any(part.startswith(a) and part[len(a):].isdigit() for a in _BLOCKER_RECOVERY_AGENT_NAMES):
        return True
    return False


def _prose_tokens_all_allowed(text: str) -> bool:
    if not text or not text.strip():
        return True
    if _CANONICAL_ARTIFACT_RE.search(text):
        return False
    chunks = re.split(
        r"[ ,;.:\-_\/&|+*~^@#$%=\(\)\[\]{}<>\"\'\`!?\s；，。：（）【】！？、—·“”‘’→]+",
        text.casefold(),
    )
    for chunk in chunks:
        if not chunk:
            continue
        if _token_is_allowed(chunk):
            continue
        parts = chunk.split("-")
        if len(parts) > 1 and all(_token_is_allowed(p) for p in parts):
            continue
        return False
    return True


def canonical_blocker_prose(task: dict[str, Any], blocker: dict[str, Any]) -> tuple[str, str]:
    """``(kind, prose)`` of one canonical blocker of ``task``, as gate classification reads them.

    The durable blocker is read, not ``task.next``, and ambiguous gate labels
    are kept before the task-note sanitizer could erase them as paths.
    `kind` is structured classification, not a code identifier: e.g.
    external_data / human_gate is not stripped as snake_case prose noise.
    """
    declared_dependencies = [str(dep).strip() for dep in (task.get("depends_on") or []) if str(dep).strip()]
    prose = blocked_task_prose_context({
        **blocker,
        "id": task.get("id"),
        "depends_on": declared_dependencies,
        "next": " ".join(str(blocker.get(key) or "") for key in ("kind", "message", "reason")),
    }, canonical_gate_prose=True)
    return str(blocker.get("kind") or "").strip().casefold().replace("_", " "), prose


def gate_prose_names_prerequisite(kind: str, prose: str) -> bool:
    """Whether canonical gate prose waits on a prerequisite -- by its kind or its words."""
    return kind == "dependency" or bool(_BLOCKER_RECOVERY_DEPENDENCY_WORDS_RE.search(prose))


def unreleased_gate_prose_reason(kind: str, prose: str, *, prerequisites_released: bool) -> str | None:
    """What canonical gate prose names that no released gate accounts for, or None.

    Fail-closed: prose is released only when everything it says is either a
    prerequisite that a nonempty, resolver-satisfied ``depends_on`` releases
    (``prerequisites_released``) -- a colon or kind label is no such
    authority -- or the routing, status and glue vocabulary every word of
    which is known. Anything else -- a human, external or operator gate, a
    path or filename that may name the awaited deliverable, a word in no
    known vocabulary or another script -- is an unresolved requirement.
    Whether known vocabulary also makes a blocked task recoverable is the
    supervisor's separate question.
    """
    if (
        kind in {"human", "human gate", "external data", "cross repo delivery"}
        or any(marker in f"{kind} {prose}" for marker in _BLOCKER_RECOVERY_HARD_GATE_MARKERS)
        or _BLOCKER_RECOVERY_HARD_GATE_WORDS_RE.search(f"{kind} {prose}")
    ):
        return "names a human, external or operator gate"
    if gate_prose_names_prerequisite(kind, prose):
        if not prerequisites_released:
            return "names a prerequisite no satisfied depends_on releases"
        prose = _BLOCKER_RECOVERY_DEPENDENCY_PROSE_RE.sub(" ", prose)
        if _CANONICAL_ARTIFACT_RE.search(prose):
            return "names a requirement no released prerequisite or routing failure accounts for"
        # A dependency-only blocker need not name routing at all; unmasked
        # IDs and non-ASCII requirements are still unknown residuals.
        prose = re.sub(r"[ ,;.:\-_/&|]+", " ", prose).strip()
    if prose and not _prose_tokens_all_allowed(prose):
        return "names a requirement no released prerequisite or routing failure accounts for"
    return None



def canonical_hard_gate_reason(
    task: dict[str, Any],
    status_snapshot: Any,
    *,
    resolver: TaskResolver | None = None,
    _seen: frozenset[str] = frozenset(),
) -> str | None:
    """Why one versioned canonical board still holds ``task`` behind a hard gate, or None.

    The hard-gate half of the supervisor's blocked-task recovery, kept here so
    that recovery and child-source admission ask one rule: the task's own
    human or independent gate, a wait on Human/Ops, gate prose in its record,
    every structured prerequisite in ``depends_on`` the board's `TaskResolver`
    (``resolver``, or one over the same board) does not report satisfied --
    named with the prerequisite's own hard gate when it has one -- and every
    open canonical blocker of the task -- its waiting party, its own gate
    fields, its kind and its prose. Prose, the task's ``blocked_reason`` and
    each open blocker's, is classified fail-closed by
    `unreleased_gate_prose_reason`: what it says has to be accounted for by
    a released prerequisite or known routing vocabulary, and prerequisite
    prose anywhere in the task needs a satisfied structured ``depends_on``.
    A board with no write revision, or whose blockers cannot be read, proves
    no blocker absent and holds the task.
    It reads only: no approval is consumed or issued, and whether a released
    gate makes the task recoverable is the supervisor's separate question.
    """
    if not isinstance(status_snapshot, dict) or not status_snapshot.get(STATUS_WRITE_REVISION_FIELD):
        return "no versioned canonical board proves its blockers"
    blockers = status_snapshot.get("blockers")
    if not isinstance(blockers, list):
        return "the canonical board's blockers cannot be read"
    gate = task_unresolved_human_or_independent_gate_reason(task)
    if gate:
        return gate
    if is_human_gate_agent(task.get("waiting_for")):
        return f"task is waiting for {task.get('waiting_for')}"
    declared_dependencies = [str(dep).strip() for dep in (task.get("depends_on") or []) if str(dep).strip()]
    if declared_dependencies:
        if resolver is None:
            resolver = TaskResolver(status_snapshot.get("tasks") or [])
        seen = _seen | {str(task.get("id") or "").strip()}
        for dep_id in declared_dependencies:
            if resolver.dependency_satisfied(dep_id):
                continue
            prerequisite = resolver.get(dep_id)
            held = (
                canonical_hard_gate_reason(prerequisite, status_snapshot, resolver=resolver, _seen=seen)
                if isinstance(prerequisite, dict) and dep_id not in seen
                else None
            )
            return f"its prerequisite {dep_id} is {resolver.dependency_status(dep_id)}, not satisfied" + (
                f", and held: {held}" if held else ""
            )
    # Every declared prerequisite is satisfied here, so a nonempty list is
    # the only release authority prerequisite prose can have.
    prerequisites_released = bool(declared_dependencies)
    context = blocked_task_prose_context({
        **task,
        "next": f"{task.get('next') or ''} {task.get('blocked_reason') or ''}",
    }, canonical_gate_prose=True)
    if any(marker in context for marker in _BLOCKER_RECOVERY_HARD_GATE_MARKERS) or _BLOCKER_RECOVERY_HARD_GATE_WORDS_RE.search(
        context
    ):
        return "task prose names a human, external or operator gate"
    if gate_prose_names_prerequisite("", context) and not prerequisites_released:
        return "task prose names a prerequisite no satisfied depends_on releases"
    # `next` is an ordinary note any command rewrites; `blocked_reason` is
    # the task's own durable gate prose and is classified as a blocker is.
    blocked_reason = str(task.get("blocked_reason") or "").strip()
    if blocked_reason:
        held = unreleased_gate_prose_reason("", blocked_task_prose_context({
            key: task.get(key) for key in ("id", "depends_on", "owner", "reviewer")
        } | {"next": blocked_reason}, canonical_gate_prose=True), prerequisites_released=prerequisites_released)
        if held:
            return f"task blocked_reason {held}"
    for blocker in blockers:
        if not isinstance(blocker, dict):
            return "a canonical blocker cannot be read"
        if blocker.get("task_id") != task.get("id") or blocker.get("status") == "resolved":
            continue
        if blocker.get("status") != "open":
            return f"a canonical blocker of the task is {blocker.get('status')!r}, neither open nor resolved"
        if is_human_gate_agent(blocker.get("waiting_for")):
            return f"an open canonical blocker of the task waits for {blocker.get('waiting_for')}"
        blocker_gate = task_unresolved_human_or_independent_gate_reason(blocker)
        if blocker_gate:
            return f"an open canonical blocker of the task: {blocker_gate}"
        held = unreleased_gate_prose_reason(
            *canonical_blocker_prose(task, blocker), prerequisites_released=prerequisites_released
        )
        if held:
            return f"an open canonical blocker of the task {held}"
    return None


def canonical_task_resolver(config: dict[str, Any]) -> TaskResolver:
    """The canonical task records, active first and then the archive."""
    return TaskResolver((load_status(config) or {}).get("tasks") or [])


def source_helper_resolver(
    config: dict[str, Any],
    parent: dict[str, Any],
    *,
    recorded: Any = None,
    publication_root: Path | None = None,
) -> Any:
    """The one rule for which other task's commits a delivery may contain.

    A commit naming another task is that task's source only when the task's
    own canonical or archived record says so: a direct child of exactly this
    parent, in the same registered repository, with an explicit literal
    nonempty `owned_paths`, a complete frozen base and review-gate head, the
    gate target recorded for that head and its own recorded branch. Nothing a
    caller supplies -- a bare `helper_parent`, prose or a list of commits --
    stands in for any of that, and it recognizes source, not approval: the
    child's own status, blockers and signoffs are left as they are. A child
    the versioned canonical board still holds behind a hard gate -- its own,
    a Human/Ops wait such as a review-churn escalation no continuation has
    released, a prerequisite its `depends_on` names that the same resolver
    does not report satisfied, or an open canonical blocker whose prose the
    fail-closed classification does not account for as a released
    prerequisite or stale routing state -- is not admitted at all, by the
    same `canonical_hard_gate_reason` blocked-task recovery asks: its source
    cannot reach review through a parent while that gate is open.

    The child's branch must also have been published at that head. The first
    scan proves it by asking the origin of `publication_root`, and only once
    that origin is the registered repository's own endpoint
    (`registered_remote_endpoint`), so another remote -- even one with the same
    owner/name -- proves nothing; afterwards the bound, the registered
    repository it was proven in included, is `recorded` with the submission, and a replay
    compares the record with it instead, so it neither needs the branch to
    still exist nor accepts a branch moved to a replacement head or a
    repository retargeted since. Every answer is derived again from the
    records; any difference from the recorded bound refuses the child, and the
    commits a bound records are compared with the scan by
    `recorded_source_helpers_error`.
    """
    toolchain = _delivery_toolchain()
    canonical: list[tuple[TaskResolver, Any]] = []
    parent_id = str(parent.get("id") or "").strip()
    repository_id = task_repository_id(config, parent) or "pantheon"
    slug = repository_slug(config, repository_id)
    endpoint = registered_remote_endpoint(config, repository_id)
    bounds = {
        str(entry.get("task_id") or ""): entry
        for entry in (recorded if isinstance(recorded, (list, tuple)) else ())
        if isinstance(entry, dict)
    }

    def resolve(child_id: str) -> Any:
        if not canonical:
            board = load_status(config) or {}
            canonical.append((TaskResolver(board.get("tasks") or []), board))
        tasks, board = canonical[0]
        child = tasks.get(child_id)
        if not isinstance(child, dict) or str(child.get("id") or "").strip() != child_id:
            return "it has no canonical or archived task record"
        if str(child.get("helper_parent") or "").strip() != parent_id:
            return f"its record is not a direct child of {parent_id}"
        if task_repository_id(config, child) != repository_id:
            return f"its record is not in the registered repository {repository_id}"
        if not (slug and endpoint):
            return f"the registered repository {repository_id} has no registered slug and endpoint"
        owned = child.get("owned_paths")
        if not (isinstance(owned, list) and owned and all(isinstance(entry, str) for entry in owned)):
            return "its record declares no literal nonempty owned_paths"
        base = str(child.get("frozen_evidence_base_sha") or "").strip().lower()
        head = str(child.get("review_gate_sha") or "").strip().lower()
        if not (_SHA_RE.fullmatch(base) and _SHA_RE.fullmatch(head)):
            return "its record has no complete frozen base and review-gate head"
        gate = child.get("review_gate_target")
        if not (
            isinstance(gate, dict)
            and str(gate.get("sha") or "").strip().lower() == head
            and str(gate.get("context") or "").strip() == "task-review-gate"
            and str(gate.get("repo_slug") or "").strip() == slug
        ):
            return f"its recorded review gate is not the task-review-gate of {head[:12]} in {slug}"
        branch = task_explicit_branch(child)
        if not branch:
            return "its record names no branch of its own"
        held = canonical_hard_gate_reason(child, board, resolver=tasks)
        if held:
            return f"its record still carries an unresolved gate: {held}"
        source = toolchain.HelperSource(
            task_id=child_id, repository=repository_id, repo_slug=slug, remote=endpoint, branch=branch,
            base_sha=base, head_sha=head, owned_paths=tuple(owned),
        )
        bound = bounds.get(child_id)
        if bound is not None:
            expected = source.binding()
            if {key: bound.get(key) for key in expected if key != "commits"} != {
                key: value for key, value in expected.items() if key != "commits"
            }:
                return "its canonical record no longer matches the bound recorded with this submission"
            return source
        if publication_root is None:
            return "no initial publication of its branch was recorded with this submission"
        origin = checkout_origin_endpoint(publication_root)
        if origin != endpoint:
            return (
                f"the origin asked for its publication is {origin or 'not configured'}, "
                f"not the registered repository {slug} at {endpoint}"
            )
        published_head = toolchain.published_branch_head(publication_root, branch)
        if str(published_head or "").strip().lower() != head:
            return f"origin does not publish {branch} at its review-gate head {head[:12]}"
        return source

    return resolve


def recorded_source_helpers_error(recorded: Any, scan: Any) -> str | None:
    """Why the child bounds a submission recorded are not exactly what a scan of its range admits.

    The bounds are immutable once recorded: neither a reviewer nor a replay of
    the same head may read them as anything but the scan's own answer, commits
    and authors included, and a resubmission does not repair a difference.
    """
    if list(scan.helpers) == recorded:
        return None
    return (
        "the recorded child sources are not the bounds the delivery scan of "
        f"{scan.base_sha[:12]}..{scan.head_sha[:12]} admits"
    )


#: Ranges whose Git authorship has already been established. Commits are
#: immutable and a commit the integration line stood on stays on it, so a
#: positive answer cannot go stale; a refusal is not cached and is asked again.
_IMMUTABLE_SOURCE_AUTHORS: dict[tuple[str, ...], tuple[str, ...]] = {}


def immutable_review_source_authors(
    config: dict[str, Any], task: dict[str, Any], submission: dict[str, Any]
) -> tuple[tuple[str, ...], str | None]:
    """Re-derive the submitted range's source authors from Git, or say why not.

    The board's `review_submission` is a record anyone with write access to the
    status file could edit, so no reviewer path takes its `source_authors` on
    trust -- not the approval, and not the queue that decides which reviewer is
    dispatched in the first place. The recorded base must be a commit the
    integration branch itself stood on -- and, for a merged submission, one it
    stood on no later than the first parent of its recorded merge commit --
    otherwise it could be moved up to hide earlier commits.
    Nor may it be a later tip that merged in part of the range: wherever it
    meets the head has to be on the integration line too. The range from it
    to the recorded head is scanned again with the same
    delivery-identity scanner submission used. The recorded authors must be
    exactly what that scan finds.
    """
    task_id = str(task.get("id") or "").strip()
    source_base = str(submission.get("source_base") or "").strip().lower()
    source_head = str(submission.get("source_head") or "").strip().lower()
    repository_id = task_repository_id(config, task) or "pantheon"
    repo_root = repository_local_path(config, repository_id)
    if repo_root is None or not repo_root.exists():
        return (), f"no local checkout of {repository_id} to read the submitted range's authorship from"
    base_branch = delivery_merge_target_branch(config, repository_id)
    expected_branch = task_branch_name(task, task_id)
    actual_branch = str(submission.get("branch") or "").strip() or None
    merge_commit = str(submission.get("merge_commit") or "").strip().lower()
    # A child source the submission recorded is judged against its canonical
    # record on every ask, before any cached answer: the Git facts of a bound
    # cannot change, but the record that made it a child of this task can.
    recorded_helpers = submission.get("source_helpers", [])
    if not (isinstance(recorded_helpers, list) and all(isinstance(entry, dict) for entry in recorded_helpers)):
        return (), "the recorded child sources are not a list of bounds"
    resolve_helper = source_helper_resolver(config, task, recorded=recorded_helpers)
    for entry in recorded_helpers:
        child_id = str(entry.get("task_id") or "")
        answer = resolve_helper(child_id)
        if isinstance(answer, str):
            return (), f"the recorded child source {child_id!r} is refused: {answer}"
    key = (
        str(repo_root), base_branch, task_id, source_base, source_head, expected_branch, actual_branch or "",
        merge_commit, json.dumps(recorded_helpers, sort_keys=True),
    )
    actual = _IMMUTABLE_SOURCE_AUTHORS.get(key)
    if actual is None:
        target_ref = f"origin/{base_branch}"
        if not _git_succeeds(repo_root, "rev-parse", "--verify", "--quiet", f"{target_ref}^{{commit}}"):
            target_ref = base_branch
        if not _git_succeeds(repo_root, "merge-base", "--is-ancestor", source_base, target_ref):
            return (), f"the recorded source base {source_base[:12]} is not in {target_ref} history"
        # After a merge, a commit from inside the delivered range is in that
        # history too; only a commit the integration line itself stood on is
        # a base submission could have read the range from.
        if not _is_integration_point(repo_root, source_base, target_ref):
            return (), (
                f"the recorded source base {source_base[:12]} is not a commit {target_ref} stood on; "
                "a commit merged in from a branch cannot be the range's base"
            )
        # A merged submission's base is a tip the line stood on before its
        # merge commit: that commit's first parent, which a first merged
        # recovery reads the range from, or an earlier tip on the same line,
        # which an unchanged head submitted while open keeps. It can be neither
        # the merge itself nor a tip from after it.
        if merge_commit:
            pre_merge_base = (
                _git_output(repo_root, "rev-parse", "--verify", "--quiet", f"{merge_commit}^1^{{commit}}")
                if _SHA_RE.fullmatch(merge_commit)
                else None
            )
            if not pre_merge_base or not _is_integration_point(repo_root, source_base, pre_merge_base.lower()):
                return (), (
                    f"the recorded source base {source_base[:12]} is not a pre-merge base of the "
                    f"recorded merge commit {merge_commit[:12]}"
                )
        # The base is also wrong when the integration line reached it by
        # merging in only part of the submitted work: a later tip that took
        # the source commit `C` but not the head `H` reads `base..H` without
        # `C`, and its author drops out of every excluded pool. The head joins
        # a faithful base where that base's own line stood, so every point
        # where they meet has to be an integration point as well. Commits Git
        # cannot relate at all are left to the scan below, which refuses a
        # head it cannot read and drops no author from unrelated history.
        merge_bases = _git_output(repo_root, "merge-base", "--all", source_base, source_head)
        for merge_base in (merge_bases or "").split():
            if not _is_integration_point(repo_root, merge_base.lower(), target_ref):
                return (), (
                    f"the recorded source base {source_base[:12]} already contains part of the submitted "
                    f"range to {source_head[:12]} through a merged branch, so it would drop that part's "
                    + (
                        "authors; only the range this head was submitted with while open names them all"
                        if merge_commit
                        else f"authors; merge {target_ref} into the task branch before submitting"
                    )
                )
        scan = _inspect_delivery_identity(
            repo_root,
            task_id=task_id,
            base=source_base,
            head=source_head,
            expected_branch=expected_branch,
            actual_branch=actual_branch,
            resolve_helper=resolve_helper,
        )
        if scan.errors:
            return (), "the submitted range fails delivery identity: " + "; ".join(scan.errors)
        bounds_error = recorded_source_helpers_error(recorded_helpers, scan)
        if bounds_error:
            return (), bounds_error
        # The authors are only those of the recorded range if the scan read
        # that range: a base or head that resolved to some other commit scanned
        # other commits, and its authors are not an answer to cache.
        scanned_base = str(scan.base_sha or "").strip().lower()
        scanned_head = str(scan.head_sha or "").strip().lower()
        if (scanned_base, scanned_head) != (source_base, source_head):
            return (), (
                f"the delivery scan read {scanned_base[:12]}..{scanned_head[:12]}, not the recorded "
                f"range {source_base[:12]}..{source_head[:12]}"
            )
        actual = tuple(scan.source_agents)
        _IMMUTABLE_SOURCE_AUTHORS[key] = actual
    recorded = tuple(str(author).strip() for author in submission.get("source_authors") or ())
    if recorded != actual:
        return (), (
            f"the recorded source authors {list(recorded)!r} are not the immutable Git authors "
            f"{list(actual)!r} of {source_base[:12]}..{source_head[:12]}"
        )
    return actual, None


def task_review_source_identity(
    config: dict[str, Any], task: dict[str, Any] | None, head: str | None = None
) -> ReviewSourceIdentity:
    """The single source-author/account-pool rule for reviewer independence.

    A review is independent of the work, not of a label. The work is the
    immutable non-merge range `review_submission` was validated against, whose
    commits each name their source actor; the account that merged a base into
    the branch and ran the finalizer is a different fact, recorded separately
    as `submitted_by`. Both matter, and so does the current owner: a reviewer
    must share no account pool with any of them. Reading only the submitter --
    or only one author of a multi-author range -- frees every other source
    pool to review its own commits.

    A reopen returns the task to `in_progress` while `review_submission` and
    `approved_head` stay on the record, so status alone is not enough: the
    submission evidence is read whenever it exists. A record in review or
    frozen without that evidence, or whose evidence is unbound, incomplete or
    names an actor that is not a registered agent, has not established who
    wrote it, and the answer is an error rather than a fallback to the owner.

    The authors are read from Git, through `immutable_review_source_authors`,
    every time this is asked: a recorded list that differs from the range it
    claims to describe is refused here, before any queue selects a reviewer
    against it. `head`, when given, is the exact head being signed, and the
    provenance has to be bound to it as well.
    """
    if not isinstance(task, dict):
        return ReviewSourceIdentity()
    owner = str(task.get("owner") or "").strip()
    submission = task.get("review_submission")
    if not (isinstance(submission, dict) and submission):
        if not owner:
            return ReviewSourceIdentity()
        statuses = normalized_status_set(
            ready_dispatch_settings(config).get("review_statuses"), list(DEFAULT_REVIEW_STATUSES)
        )
        statuses.update(DEFAULT_SUBMITTED_AUTHOR_STATUSES)
        if not task_closeout_is_frozen(config, task) and str(task.get("status") or "").strip().lower() not in statuses:
            return ReviewSourceIdentity()
        return ReviewSourceIdentity(
            submitted=True,
            error="the task is in review without a validated review submission naming its source authors",
        )

    submitter = str(submission.get("submitted_by") or "").strip()
    raw_authors = submission.get("source_authors")
    authors = (
        tuple(str(author).strip() for author in raw_authors)
        if isinstance(raw_authors, (list, tuple))
        else ()
    )
    identity = ReviewSourceIdentity(submitted=True, submitter=submitter, source_authors=authors)
    remote_sha = str(submission.get("remote_sha") or "").strip().lower()
    source_head = str(submission.get("source_head") or "").strip().lower()
    source_base = str(submission.get("source_base") or "").strip().lower()
    if not submitter:
        return identity._replace(error="the review submission does not record its genuine submitter")
    if not authors or any(not author for author in authors):
        return identity._replace(error="the review submission names no immutable source authors")
    if not _SHA_RE.fullmatch(source_head) or source_head != remote_sha:
        return identity._replace(error="the recorded source authors are not bound to the submitted head")
    if not _SHA_RE.fullmatch(source_base):
        return identity._replace(error="the recorded source authors are not bound to a validated base")
    if head is not None and source_head != str(head or "").strip().lower():
        return identity._replace(error="the recorded source authors are not bound to the head being approved")
    authors, git_error = immutable_review_source_authors(config, task, submission)
    if git_error:
        return identity._replace(error=git_error)

    agents: set[str] = set()
    pools: set[str] = set()
    actors = [("source author", author) for author in authors]
    actors.append(("submitter", submitter))
    if owner:
        actors.append(("owner", owner))
    for role, name in actors:
        if role != "source author" and is_human_gate_agent(name):
            agents.add(normalize_agent_id(name))
            continue
        pool = _registered_actor_pool(config, name)
        if not pool:
            return identity._replace(
                error=f"{role} {name!r} is not a registered agent with an account pool"
            )
        agents.update({normalize_agent_id(name), _registered_actor_id(config, name)})
        pools.add(pool)
    return identity._replace(agents=frozenset(agents), pools=frozenset(pools))


def review_source_block_reason(
    config: dict[str, Any], task: dict[str, Any] | None, reviewer: str | None, head: str | None = None
) -> str | None:
    """Why `reviewer` may not review this task's submitted work, or None.

    The one question every reviewer path asks -- the normalizer, failover,
    dispatch readiness and the canonical approval -- so that none of them can
    admit a reviewer another would refuse.
    """
    identity = task_review_source_identity(config, task, head)
    if not identity.submitted:
        return None
    if identity.error:
        return identity.error
    if is_human_gate_agent(reviewer):
        return None
    reviewer_ids = {normalize_agent_id(reviewer or ""), _registered_actor_id(config, reviewer or "")}
    if reviewer_ids & identity.agents:
        return f"reviewer {reviewer} is itself a source author, submitter or owner of the submitted work"
    pool = _registered_actor_pool(config, reviewer or "")
    if not pool:
        return f"reviewer {reviewer!r} is not a registered agent with an account pool"
    if pool in identity.pools:
        return (
            f"reviewer {reviewer} shares account pool {pool} with a source author, "
            "submitter or owner of the submitted work"
        )
    return None


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
    declared_provider = str(agent.get("provider") or "").strip()
    provider_id = declared_provider or agent_id
    canonical_key, provider_cfg = provider_config_entry(config, provider_id)
    if not isinstance(provider_cfg, dict) or not provider_cfg:
        return set()
    identities = {
        normalize_agent_id(provider_id),
        normalize_agent_id(str(canonical_key or "")),
        normalize_agent_id(str(agent.get("adapter") or "")),
        normalize_agent_id(str(provider_cfg.get("delivery_mode") or "")),
        normalize_agent_id(str(provider_cfg.get("adapter") or provider_cfg.get("type") or "")),
    }
    identities.discard("")
    return identities


def agent_provider_identity_conflict(config: dict[str, Any], agent_name: str | None) -> str | None:
    """Why this agent record names one provider but runs another, or None.

    `agent_provider_identity_ids` returns a *union* of aliases on purpose:
    `antigravity2` and `antigravity` are the same lane under two spellings, and
    a policy naming either must match. That union is also the hole. An agent
    record that omits `provider` has its provider inferred from its own agent
    id, so a lane called `claude` carrying `adapter: codex` resolves to the
    identity set {claude, claude_cli, codex} and satisfies an owner rule written
    for Claude -- while `codex exec` is what actually launches.

    The adapter is what runs. When it is not one of the identities the record's
    *configured* provider entry resolves to, the record says two different
    things about who does the work, and the friendlier reading must not be the
    one that wins. That is a configuration error, so it is described here and
    the caller fails closed rather than guessing.
    """
    agent_id = normalize_agent_id(agent_name or "")
    if not agent_id:
        return None
    agent = (config.get("agents", {}) or {}).get(agent_id, {}) or {}
    adapter = normalize_agent_id(str(agent.get("adapter") or ""))
    if not adapter:
        return None
    declared_provider = str(agent.get("provider") or "").strip()
    provider_id = declared_provider or agent_id
    canonical_key, provider_cfg = provider_config_entry(config, provider_id)
    provider_identities = {
        normalize_agent_id(provider_id),
        normalize_agent_id(str(canonical_key or "")),
        normalize_agent_id(str(provider_cfg.get("delivery_mode") or "")),
        normalize_agent_id(str(provider_cfg.get("adapter") or provider_cfg.get("type") or "")),
    }
    provider_identities.discard("")
    if adapter in provider_identities:
        return None
    source = (
        f"provider {normalize_agent_id(provider_id)}"
        if declared_provider
        else f"provider inferred from the agent id {agent_id} (no `provider` is declared)"
    )
    resolved = ", ".join(sorted(provider_identities)) or "nothing"
    return f"adapter {adapter} is not one of the identities its configured {source} resolves to ({resolved})"


def provider_dispatch_group_id(config: dict[str, Any], provider: str | None) -> str:
    provider_id = normalize_agent_id(provider or "")
    if not provider_id:
        return ""
    provider_cfg = provider_config(config, provider)
    group = (
        provider_cfg.get("quota_group")
        or provider_cfg.get("dispatch_group")
        or provider_cfg.get("account_group")
    )
    return normalize_agent_id(str(group or provider_id))


def agent_provider_id(config: dict[str, Any], agent_id: str | None) -> str:
    normalized = normalize_agent_id(agent_id or "")
    if not normalized:
        return ""
    agent = (config.get("agents", {}) or {}).get(normalized, {}) or {}
    return normalize_agent_id(str(agent.get("provider") or normalized))


def agent_quota_group_id(config: dict[str, Any], agent_id: str | None) -> str:
    """Return the real account pool for an execution identity.

    `quota_group` was historically provider-scoped, which made aliases such as
    Antigravity2..7 look like independent accounts.  An explicit agent
    `account_pool` is authoritative and lets multiple logical roles share one
    provider account, quota budget, and worker-slot set.

    This is the leaf implementation; `supervisor` re-exports it under the names
    its callers already use. It lives here so the canonical CLI can ask "are
    these two names the same account?" before it writes an assignment, without
    importing the supervisor and without growing a second answer to it.
    """
    normalized = normalize_agent_id(agent_id or "")
    agent = (config.get("agents", {}) or {}).get(normalized, {}) or {}
    explicit_pool = agent.get("account_pool") or agent.get("quota_group")
    if explicit_pool:
        return normalize_agent_id(str(explicit_pool))
    provider_id = agent_provider_id(config, agent_id)
    return provider_dispatch_group_id(config, provider_id or agent_id)


def agent_account_pool_id(config: dict[str, Any], agent_id: str | None) -> str:
    """Semantic alias used for independence checks and dashboard reporting."""
    return agent_quota_group_id(config, agent_id)


def review_is_independent(config: dict[str, Any], owner: str | None, reviewer: str | None) -> bool:
    owner_pool = agent_account_pool_id(config, owner)
    reviewer_pool = agent_account_pool_id(config, reviewer)
    return bool(owner_pool and reviewer_pool and owner_pool != reviewer_pool)


def known_provider_identity_ids(config: dict[str, Any]) -> set[str]:
    """Provider ids a policy may legitimately name in this configuration.

    A policy entry naming a provider this fleet does not have is not a harmless
    no-op: written into a `providers` list it silently shrinks the allowed set,
    and written as the only entry it silently empties it. Validating against the
    ids that actually resolve is what turns that into a visible config error.
    """
    known: set[str] = set()
    for provider_id, provider_cfg in (config.get("providers", {}) or {}).items():
        if not isinstance(provider_cfg, dict):
            continue
        known.add(normalize_agent_id(str(provider_id)))
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
    if settings is None:
        return None
    if isinstance(settings, dict) and settings.get("enabled") is False:
        return None
    return settings if isinstance(settings, dict) else {"__raw__": settings}


def role_provider_policy_error(config: dict[str, Any]) -> str | None:
    """Describe why an enabled policy cannot be applied, or None when it is sound.

    An enabled-but-unreadable policy is the dangerous case: the rule that says
    "only Codex reviews" is exactly the rule a typo would delete, and a deleted
    rule looks identical to no rule at all. So a malformed policy denies rather
    than degrades, and says which key it could not read.
    """
    settings = ready_dispatch_settings(config).get(ROLE_PROVIDER_POLICY_KEY)
    if settings is None:
        return None
    if isinstance(settings, dict) and settings.get("enabled") is False:
        return None
    if not isinstance(settings, dict):
        return "role_provider_policy must be an object"
    if not settings:
        return "role_provider_policy cannot be empty when present"
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
    if not rules:
        return "rules must contain at least one rule"
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
    grants_new_authority: bool = False,
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

    `grants_new_authority` separates the two questions a caller can be asking.
    The default -- False -- asks "may this actor keep doing what the record
    already says it does", and a frozen closeout is exempt from it, because
    there the answer is history rather than a choice. True asks "may this actor
    be given something it does not have yet": a different agent written onto an
    approved task, or a fresh approval signature. A pinned head is no
    justification for either, so the exemption does not apply to those.
    """
    if role_provider_policy_settings(config) is None:
        return None
    error = role_provider_policy_error(config)
    if error:
        return f"role_provider_policy is enabled but malformed ({error}); dispatch fails closed"
    # A frozen closeout is outside the policy for the existing actor recorded on it.
    # Here the policy would not be choosing who should do the work -- the work is done and
    # its head is pinned. Applying it would evict the owner from finalizing its
    # own approved commit, or rewrite which reviewer approved it, which is the
    # one thing a change of policy must never do retroactively. It stops being an
    # exemption the moment the call is about to hand out something new, or when
    # evaluating a different actor that is not already recorded on the frozen task.
    if not grants_new_authority and task_closeout_is_frozen(config, task):
        recorded_actor = str((task or {}).get(role or "") or "").strip()
        if recorded_actor and normalize_agent_id(recorded_actor) == normalize_agent_id(agent_name or ""):
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
    conflict = agent_provider_identity_conflict(config, name)
    if conflict:
        # Matching is a union over aliases, so a record that names two different
        # providers would be admitted by whichever of them the rule happens to
        # allow. Refuse before that intersection is taken.
        return f"{label} provider identity is contradictory ({conflict}), so {scope} fails closed"
    if not identities & allowed:
        return (
            f"{label} provider {', '.join(sorted(identities))} is not permitted for "
            f"{scope} (allowed: {', '.join(sorted(allowed))})"
        )
    return None


def dispatch_reason_priority(reason: str | None) -> int | None:
    return DISPATCH_REASON_PRIORITIES.get(str(reason or ""))


def dispatch_priority_reason(priority: int | None) -> str | None:
    """The dispatch reason a lane priority stands for, or `None` if unknown.

    `dispatch_reason_priority` read backwards. Callers that hold a priority --
    the number `dispatch_priority_for_task` returns -- and need the role that
    lane puts an agent in go through here rather than mapping the number
    themselves, so there stays one statement of which lane is which.
    """
    if priority is None or isinstance(priority, bool):
        return None
    return DISPATCH_PRIORITY_REASONS.get(priority)


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
