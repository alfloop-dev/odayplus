#!/usr/bin/env python3
"""Validate that an entire task delivery has one immutable identity.

Every non-merge commit between the integration base and delivery head must
carry the requested task id and valid owner/reviewer trailers.  Callers may
also bind the delivery to an expected branch.  This prevents a reused branch
or a correct-looking HEAD from hiding older commits for another task.

The same scan is also the record of who wrote the delivery. Each non-merge
commit names its source actor in its ``LLM-Agent`` trailer, and the set of
those actors is what an independent reviewer has to be independent of -- not
whoever later merged a base into the branch or submitted the PR. The scan
reports them exactly as written; resolving a name to a registered account is
the caller's job, so an unknown label stays visible instead of being guessed.

A commit of another task is not part of the delivery unless the caller's
``resolve_helper`` answers for that task: it names a direct child the
canonical record composes into this one, with its frozen range and declared
scope. The scan then proves the commit is genuinely that child's -- its own
trailers, inside the child's frozen range, whose whole net change stays in
the child's scope and is contained in this delivery -- and reports the bound
it admitted, so the caller can keep exactly that bound with the submission.
The child's authors are authors of the delivery like any other.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .check_commit_scope import files_outside_scope, net_changed_files, validate_scope
    from .check_commit_trailers import parse_trailers, validate_message
except ImportError:  # Direct script execution from task_finalize.sh.
    from check_commit_scope import files_outside_scope, net_changed_files, validate_scope
    from check_commit_trailers import parse_trailers, validate_message


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise ValueError(detail)
    return result.stdout.strip()


#: A complete object name. It names one immutable commit, never a branch.
_FULL_OID = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")


def resolve_base_ref(repo: Path, base: str) -> str:
    """Prefer the remote integration ref while retaining offline compatibility.

    A branch name such as ``dev`` is read as ``origin/dev`` when that exists.
    A complete object name is not a branch: it is the commit it names, and
    reading it as ``origin/<sha>`` would let anyone who can create a ref of
    that spelling move the base of an immutable range somewhere else.
    """
    candidates = [base]
    if not _FULL_OID.fullmatch(base) and not base.startswith(("origin/", "refs/")):
        candidates.insert(0, f"origin/{base}")
    for candidate in candidates:
        try:
            _git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}")
            return candidate
        except ValueError:
            continue
    raise ValueError(f"integration base {base!r} does not resolve to a commit")


@dataclass(frozen=True)
class HelperSource:
    """A direct child task's source contribution, as its canonical record states it.

    ``base_sha``..``head_sha`` is the child's frozen range and ``owned_paths``
    its declared literal scope, in the registered ``repository`` (``repo_slug``,
    published at endpoint ``remote``) its publication was proven in. It
    recognizes source only: it is not an approval of the child.
    """

    task_id: str
    repository: str
    repo_slug: str
    remote: str
    branch: str
    base_sha: str
    head_sha: str
    owned_paths: tuple[str, ...]

    def binding(self, commits: tuple[tuple[str, str], ...] = ()) -> dict:
        """The immutable bound a review submission records for this child."""
        return {
            "task_id": self.task_id,
            "repository": self.repository,
            "repo_slug": self.repo_slug,
            "remote": self.remote,
            "branch": self.branch,
            "source_base": self.base_sha,
            "source_head": self.head_sha,
            "owned_paths": list(self.owned_paths),
            "commits": [[sha, agent] for sha, agent in commits],
        }


#: Answers a foreign ``Task-ID`` with that child's source, or why it is none.
HelperResolver = Callable[[str], "HelperSource | str"]


@dataclass(frozen=True)
class DeliveryIdentity:
    """What one immutable ``base..head`` delivery range proves.

    ``source_agents`` is only meaningful when ``errors`` is empty: a range that
    failed validation has not established who wrote it.
    """

    errors: list[str] = field(default_factory=list)
    base_sha: str = ""
    head_sha: str = ""
    #: ``(commit sha, LLM-Agent)`` for every non-merge commit, oldest first.
    commits: tuple[tuple[str, str], ...] = ()
    #: The bound of every child source admitted into the range, in first-commit order.
    helpers: tuple[dict, ...] = ()

    @property
    def source_agents(self) -> tuple[str, ...]:
        """Every distinct source actor of the range, in first-commit order."""
        return tuple(dict.fromkeys(agent for _sha, agent in self.commits if agent))


def inspect_delivery_identity(
    repo: Path,
    *,
    task_id: str,
    base: str,
    head: str,
    expected_branch: str | None = None,
    actual_branch: str | None = None,
    resolve_helper: HelperResolver | None = None,
) -> DeliveryIdentity:
    """Validate the delivery range and report the source actor of every commit.

    Without ``resolve_helper`` every commit must be this task's own. With it,
    a commit naming another task is admitted only as that child's source.
    """
    errors: list[str] = []
    repo = repo.resolve()
    task_id = task_id.strip()
    if not task_id:
        return DeliveryIdentity(errors=["task id is empty"])
    if expected_branch and actual_branch and expected_branch != actual_branch:
        errors.append(
            f"branch {actual_branch!r} does not match recorded task branch {expected_branch!r}"
        )

    try:
        base_ref = resolve_base_ref(repo, base)
        base_sha = _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
        if _FULL_OID.fullmatch(base) and base_sha.lower() != base.lower():
            raise ValueError(f"integration base {base[:12]} resolved to a different commit {base_sha[:12]}")
        if _FULL_OID.fullmatch(head) and head_sha.lower() != head.lower():
            raise ValueError(f"delivery head {head[:12]} resolved to a different commit {head_sha[:12]}")
        # The range is listed between the resolved commits, not the names, so
        # it is exactly the range ``base_sha``/``head_sha`` report.
        commits_text = _git(repo, "rev-list", "--reverse", "--no-merges", f"{base_sha}..{head_sha}")
    except ValueError as exc:
        return DeliveryIdentity(errors=[*errors, str(exc)])

    commits = [line for line in commits_text.splitlines() if line]
    if not commits:
        return DeliveryIdentity(
            errors=[*errors, f"delivery range {base_ref}..{head} contains no non-merge commits"],
            base_sha=base_sha,
            head_sha=head_sha,
        )

    sources: list[tuple[str, str]] = []
    commit_tasks: dict[str, str] = {}
    children: dict[str, tuple[HelperSource | None, list[str]]] = {}
    for commit in commits:
        try:
            message = _git(repo, "show", "-s", "--format=%B", commit)
        except ValueError as exc:
            errors.append(f"commit {commit[:12]} cannot be inspected: {exc}")
            continue
        trailers = parse_trailers(message)
        commit_task = trailers.get("task-id", "").strip()
        commit_tasks[commit] = commit_task
        sources.append((commit, trailers.get("llm-agent", "").strip()))
        scope_task = task_id
        if resolve_helper is not None and commit_task and commit_task.lower() != task_id.lower():
            if commit_task not in children:
                children[commit_task] = _child_source(
                    repo, resolve_helper, commit_task, head_sha=head_sha, delivery=set(commits)
                )
            child, child_range = children[commit_task]
            if child is None:
                # Not a child source, so it is judged as this task's own
                # commit and keeps every refusal that already gives.
                errors.append(f"commit {commit[:12]}: {child_range[0]}")
            elif commit not in child_range:
                errors.append(
                    f"commit {commit[:12]}: names child {commit_task!r} but is not in its frozen range "
                    f"{child.base_sha[:12]}..{child.head_sha[:12]}"
                )
                scope_task = child.task_id
            else:
                scope_task = child.task_id
        violations = validate_message(
            message,
            task_id=scope_task,
            allow_maintenance_skip=False,
            require_subject_length_limit=False,
        )
        errors.extend(f"commit {commit[:12]}: {violation}" for violation in violations)
    helpers: list[dict] = []
    for child, child_range in children.values():
        if child is None:
            continue
        # The frozen range is the child's own work: a commit inside it that
        # names some other task is not the child's to contribute.
        for commit in child_range:
            if commit_tasks.get(commit, "").lower() != child.task_id.lower():
                errors.append(
                    f"commit {commit[:12]}: is in {child.task_id}'s frozen range but names task "
                    f"{commit_tasks.get(commit, '')!r}"
                )
        helpers.append(child.binding(tuple(source for source in sources if source[0] in child_range)))
    return DeliveryIdentity(
        errors=errors,
        base_sha=base_sha,
        head_sha=head_sha,
        commits=tuple(sources),
        helpers=tuple(helpers),
    )


def _child_source(
    repo: Path,
    resolve_helper: HelperResolver,
    child_id: str,
    *,
    head_sha: str,
    delivery: set[str],
) -> tuple[HelperSource | None, list[str]]:
    """The child's admitted source and its frozen commits, or ``None`` and why not.

    The canonical answer only names the child's frozen range and scope; what
    is in that range is read from Git here. The range has to be exactly the
    commits it names, wholly contained in this delivery, and its net change
    -- merges included, so nothing rides in through a merge resolution --
    has to stay inside the child's declared scope.
    """
    answer = resolve_helper(child_id)
    if not isinstance(answer, HelperSource):
        return None, [f"names task {child_id!r}, which is not a source child of this delivery: {answer}"]
    child = answer
    if child.task_id != child_id:
        return None, [f"names task {child_id!r}, but its source answer is for {child.task_id!r}"]
    bound = f"{child_id} frozen range {child.base_sha[:12]}..{child.head_sha[:12]}"
    if not (_FULL_OID.fullmatch(child.base_sha) and _FULL_OID.fullmatch(child.head_sha)):
        return None, [f"{bound} is not a pair of complete commit names"]
    try:
        for name in (child.base_sha, child.head_sha):
            if _git(repo, "rev-parse", "--verify", f"{name}^{{commit}}").lower() != name.lower():
                raise ValueError(f"{name[:12]} resolves to a different commit")
        for ancestor, descendant, what in (
            (child.base_sha, child.head_sha, "its own head"),
            (child.head_sha, head_sha, f"delivery head {head_sha[:12]}"),
        ):
            if subprocess.run(
                ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo, capture_output=True
            ).returncode != 0:
                raise ValueError(f"{ancestor[:12]} is not an ancestor of {what}")
        child_range = [
            line
            for line in _git(repo, "rev-list", "--reverse", "--no-merges", f"{child.base_sha}..{child.head_sha}")
            .splitlines()
            if line
        ]
        changed = net_changed_files(child.base_sha, child.head_sha, cwd=str(repo))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        return None, [f"{bound} cannot be proven: {exc}"]
    if not child_range:
        return None, [f"{bound} contains no non-merge commits"]
    outside = [commit for commit in child_range if commit not in delivery]
    if outside:
        return None, [
            f"{bound} is only partly in this delivery; missing " + ", ".join(c[:12] for c in outside)
        ]
    scope = list(child.owned_paths)
    problems = validate_scope(scope) if all(isinstance(entry, str) for entry in scope) else ["not literal"]
    if problems:
        return None, [f"{bound} has no usable literal owned_paths: " + "; ".join(problems)]
    leaks = files_outside_scope(changed, scope)
    if leaks:
        return None, [
            f"{bound} changes {len(leaks)} net path(s) outside its owned_paths: "
            + ", ".join(repr(path) for path in leaks)
        ]
    return child, child_range


def published_branch_head(repo: Path, branch: str) -> str | None:
    """The commit ``origin`` publishes ``branch`` at now, or None.

    Asked of the remote itself, not of a local ``refs/remotes`` copy that any
    checkout can point anywhere: this is the initial publication proof of a
    child source, so it has to be the published fact. Which repository that
    origin is, is the caller's to have established first.
    """
    ref = f"refs/heads/{branch}"
    try:
        listing = _git(repo, "ls-remote", "--heads", "origin", ref)
    except ValueError:
        return None
    for line in listing.splitlines():
        sha, _, name = line.partition("\t")
        if name == ref and _FULL_OID.fullmatch(sha):
            return sha.lower()
    return None


def canonical_helper_resolver(repo: Path, task_id: str) -> HelperResolver:
    """The canonical child-source rule, for a delivery checked before it is published.

    The same rule review submission and every reviewer path use, read from
    the live status root's records, from this checkout -- once its origin is
    the registered repository, which is asked for the published branch.
    Nothing is loaded unless a commit actually names another task, and if the
    records cannot be read no child is admitted.
    """
    resolved: list[HelperResolver] = []

    def resolve(child_id: str) -> HelperSource | str:
        if not resolved:
            try:
                orchestrator = Path(__file__).resolve().parents[2] / ".orchestrator"
                if str(orchestrator) not in sys.path:
                    sys.path.insert(0, str(orchestrator))
                from common import load_config_for_status_root
                from dispatch_policy import canonical_task_resolver, source_helper_resolver
                from task_archive import STATUS_ROOT

                config = load_config_for_status_root(STATUS_ROOT)
                parent = canonical_task_resolver(config).get(task_id)
                if parent is None:
                    raise ValueError(f"{task_id} has no canonical task record")
                resolved.append(source_helper_resolver(config, parent, publication_root=repo))
            except Exception as exc:  # Any unreadable context admits no child.
                reason = f"the canonical task records cannot be read ({exc})"
                resolved.append(lambda _child_id: reason)
        return resolved[0](child_id)

    return resolve


def validate_delivery_identity(
    repo: Path,
    *,
    task_id: str,
    base: str,
    head: str,
    expected_branch: str | None = None,
    actual_branch: str | None = None,
    resolve_helper: HelperResolver | None = None,
) -> list[str]:
    """Return delivery identity violations; empty means the range is coherent."""
    return inspect_delivery_identity(
        repo,
        task_id=task_id,
        base=base,
        head=head,
        expected_branch=expected_branch,
        actual_branch=actual_branch,
        resolve_helper=resolve_helper,
    ).errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--expected-branch")
    parser.add_argument("--actual-branch")
    args = parser.parse_args(argv)
    errors = validate_delivery_identity(
        Path(args.repo),
        task_id=args.task_id,
        base=args.base,
        head=args.head,
        expected_branch=args.expected_branch,
        actual_branch=args.actual_branch,
        resolve_helper=canonical_helper_resolver(Path(args.repo).resolve(), args.task_id.strip()),
    )
    if errors:
        print("check_task_delivery_identity: delivery rejected:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # The canonical rule imports this module by name; run as a script it must
    # find this one, so the child sources it answers are of the scanner's type.
    sys.modules.setdefault("check_task_delivery_identity", sys.modules[__name__])
    raise SystemExit(main())
