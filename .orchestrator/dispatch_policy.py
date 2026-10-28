from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

from common import normalize_agent_id
from multi_repo_registry import repository_local_path, resolve_repository, task_repository_id
from provider_runtime import provider_config, provider_config_entry

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


def _inspect_delivery_identity(repo_root: Path, **kwargs: Any) -> Any:
    """The delivery-identity scanner `submit_review` validates a range with."""
    git_dir = Path(__file__).resolve().parents[1] / "delivery_toolchain" / "git"
    if str(git_dir) not in sys.path:
        sys.path.insert(0, str(git_dir))
    from check_task_delivery_identity import inspect_delivery_identity

    return inspect_delivery_identity(repo_root, **kwargs)


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
    key = (
        str(repo_root), base_branch, task_id, source_base, source_head, expected_branch, actual_branch or "",
        merge_commit,
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
        )
        if scan.errors:
            return (), "the submitted range fails delivery identity: " + "; ".join(scan.errors)
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
