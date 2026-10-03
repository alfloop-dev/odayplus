"""Verify the ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001 recovery evidence.

Keeps artifact_missing / approval_missing / history_unknown facts apart, binds
every registry, history, board, source and training-requirement claim to the
bytes of a saved receipt or cited Git blob, and refuses any document that turns
a bounded investigation into an unbounded absence claim, an unattested
recovered/approved state, model readiness, a deploy GO, or a cloud write.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

EVIDENCE_DIR = Path(__file__).resolve().parent
EVIDENCE_PATH = EVIDENCE_DIR / "recovery_evidence.json"
REPO_ROOT = EVIDENCE_DIR.parents[3]

READINESS_MODELS = {
    "forecast_revenue_interval",
    "dealroom_avm",
    "sitescore_propensity",
    "heatzone_priority",
}
REGISTRY_STATES = {"absent", "registered", "not_read_current"}
ARTIFACT_STATES = {"recovered", "artifact_missing", "not_read_current"}
APPROVAL_STATES = {"approved", "approval_missing", "not_read_current"}
# This bounded read-only investigation saved no artifact bytes and no training,
# approval or rollback provenance it could check, so it cannot attest these
# positive states; a hash shape or a non-empty ref is not evidence of either.
UNATTESTABLE = (
    "this bounded investigation saved no artifact bytes or "
    "training/approval/rollback provenance to verify it"
)
DAY_SPAN = re.compile(r"\b\d+\s+(?:consecutive|contiguous)(?:\s+[\w/]+)*\s+days\b", re.I)
HISTORY_STATES = {"history_unknown", "history_current"}
BOARD_FIELDS = ("status", "owner", "reviewer", "last_update", "non_dispatchable")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_REF = re.compile(r"^([0-9a-f]{40}):(.+)$")
# A bounded read-only lookup can only report absence within what it observed.
UNBOUNDED_ABSENCE = re.compile(
    r"never existed|never produced|was ever produced|no approval exists|"
    r"nothing to (?:recover|restore)|none is needed|no such artifact exists|"
    r"exists? anywhere",
    re.IGNORECASE,
)
_MISSING = object()

BlobResolver = Callable[[str], bytes | None]
CommitTimeResolver = Callable[[str], datetime | None]


def _sha(value: object) -> bool:
    return isinstance(value, str) and bool(SHA256.match(value))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _git(root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def git_blob_resolver(root: Path) -> BlobResolver:
    return lambda ref: _git(root, "cat-file", "blob", ref)


def git_commit_time_resolver(root: Path) -> CommitTimeResolver:
    def resolve(sha: str) -> datetime | None:
        out = _git(root, "show", "-s", "--format=%cI", sha)
        return _time(out.decode().strip()) if out else None

    return resolve


def evidence_committed_at(root: Path) -> datetime | None:
    """Commit time of the evidence file, only when the working copy matches HEAD."""
    rel = str(EVIDENCE_PATH.relative_to(REPO_ROOT))
    if _git(root, "diff", "--quiet", "HEAD", "--", rel) is None:
        return None
    out = _git(root, "log", "-1", "--format=%cI", "--", rel)
    return _time(out.decode().strip()) if out else None


def _lookup(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, Mapping) or part not in data:
            return _MISSING
        data = data[part]
    return data


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _same(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


class _Context:
    def __init__(
        self,
        doc: Mapping[str, Any],
        root: Path,
        git_blob: BlobResolver,
        commit_time: CommitTimeResolver,
    ) -> None:
        self.doc = doc
        self.root = root
        self.git_blob = git_blob
        self.commit_time = commit_time
        self.errors: list[str] = []
        self.captured_at = _time(doc.get("captured_at"))
        self.receipts: dict[str, dict[str, Any]] = {}
        self.sources: dict[str, tuple[str, bytes]] = {}

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def not_after_capture(self, label: str, value: object) -> None:
        when = _time(value)
        if when is None:
            self.fail(f"{label}: invalid timestamp {value!r}")
        elif self.captured_at is not None and when > self.captured_at:
            self.fail(f"{label}: timestamp {value} is after captured_at")

    def load_receipts(self) -> None:
        for receipt in self.doc.get("saved_receipts") or []:
            path = str(receipt.get("path", ""))
            target = self.root / path
            if not _sha(receipt.get("sha256")):
                self.fail(f"saved receipt {path} lacks sha256")
            elif not target.is_file():
                self.fail(f"saved receipt {path} is missing")
            elif _digest(target.read_bytes()) != receipt["sha256"]:
                self.fail(f"saved receipt {path} sha256 mismatch")
            else:
                body = json.loads(target.read_text(encoding="utf-8"))
                own_time = body.get("observed_at", body.get("captured_at"))
                if receipt.get("observed_at") != own_time:
                    self.fail(f"saved receipt {path} observed_at differs from its contents")
                self.receipts[Path(path).name] = body
            self.not_after_capture(f"saved receipt {path}", receipt.get("observed_at"))

    def load_sources(self) -> None:
        for source in self.doc.get("sources") or []:
            sid = str(source.get("id"))
            ref = str(source.get("ref", ""))
            match = GIT_REF.match(ref)
            if not match:
                self.fail(f"source {sid}: ref must be <full sha>:<path>")
                continue
            expected = source.get("sha256")
            if not _sha(expected):
                self.fail(f"source {sid}: lacks sha256")
                continue
            copy = self.root / str(source.get("saved_copy", ""))
            if not source.get("saved_copy") or not copy.is_file():
                self.fail(f"source {sid}: saved copy is missing")
                continue
            data = copy.read_bytes()
            if _digest(data) != expected:
                self.fail(f"source {sid}: saved copy sha256 mismatch")
                continue
            blob = self.git_blob(ref)
            if blob is not None and _digest(blob) != expected:
                self.fail(f"source {sid}: git blob {ref} sha256 mismatch")
                continue
            self.not_after_capture(f"source {sid}", source.get("ref_committed_at"))
            committed = self.commit_time(match.group(1))
            if committed is not None and committed != _time(source.get("ref_committed_at")):
                self.fail(f"source {sid}: ref_committed_at differs from the git commit")
            self.sources[sid] = (ref, data)

    def bind(self, label: str, binding: Mapping[str, Any]) -> str | None:
        """Check facts/contains of a binding against its saved source; return its ref."""
        sid = binding.get("source")
        if sid not in self.sources:
            self.fail(f"{label}: source {sid!r} is not a verified saved source")
            return None
        ref, data = self.sources[sid]
        text = data.decode("utf-8")
        for needle in binding.get("contains") or []:
            if needle not in text:
                self.fail(f"{label}: source {sid} does not contain {needle!r}")
        facts = binding.get("facts") or {}
        if facts:
            body = json.loads(text)
            for path, value in facts.items():
                if not _same(_lookup(body, path), value):
                    self.fail(f"{label}: fact {path}={value!r} not in source {sid}")
        return ref


def _check_registry(ctx: _Context, name: str, registry: Mapping[str, Any]) -> None:
    state = registry.get("state")
    if state not in REGISTRY_STATES:
        ctx.fail(f"{name}: unknown registry state {state!r}")
        return
    if state == "not_read_current":
        if registry.get("historical_source") not in ctx.sources:
            ctx.fail(f"{name}: not_read_current needs a verified historical source")
        return
    if not registry.get("scope"):
        ctx.fail(f"{name}: registry conclusion needs an observed scope")
    readback = registry.get("readback") or {}
    receipt = ctx.receipts.get(str(readback.get("receipt")))
    if receipt is None:
        ctx.fail(f"{name}: registry readback is not a verified saved receipt")
        return
    url = urlparse(str(receipt.get("url", "")))
    receipt_model = (parse_qs(url.query).get("name") or [None])[0]
    if readback.get("model_name") != name or receipt_model != name:
        ctx.fail(f"{name}: registry readback receipt names a different model ({receipt_model})")
    if readback.get("mlflow_host") != url.hostname:
        ctx.fail(f"{name}: registry readback host differs from the receipt")
    environment = readback.get("environment")
    if not environment or f"{environment} MLflow" not in str(receipt.get("interpretation", "")):
        ctx.fail(f"{name}: registry readback environment not stated by the receipt")
    if readback.get("observed_at") != receipt.get("observed_at"):
        ctx.fail(f"{name}: registry readback time differs from the receipt")
    status = receipt.get("http_status")
    if not _same(readback.get("http_status"), status):
        ctx.fail(f"{name}: registry readback status differs from the receipt")
    response = receipt.get("response") or {}
    if state == "absent" and not (
        status == 404 and response.get("error_code") == "RESOURCE_DOES_NOT_EXIST"
    ):
        ctx.fail(f"{name}: absent contradicts the receipt status {status}")
    if state == "registered" and not (
        status == 200 and (response.get("registered_model") or {}).get("name") == name
    ):
        ctx.fail(f"{name}: registered contradicts the receipt status {status}")


def _check_scoped(ctx: _Context, name: str, kind: str, block: Mapping[str, Any]) -> None:
    if not block.get("observed_scope"):
        ctx.fail(f"{name}: {kind} needs an observed_scope")
    if not isinstance(block.get("unobserved_scope"), list):
        ctx.fail(f"{name}: {kind} needs an explicit unobserved_scope list")
    elif block.get("legacy_bucket_access") == "access_unknown" and not block["unobserved_scope"]:
        ctx.fail(f"{name}: access_unknown scope must stay listed as unobserved")


def _check_history(ctx: _Context, name: str, history: Mapping[str, Any]) -> None:
    state = history.get("state")
    if state not in HISTORY_STATES:
        ctx.fail(f"{name}: unknown history state {state!r}")
        return
    if history.get("last_measured"):
        ctx.bind(f"{name} last_measured", history["last_measured"])
    if state == "history_unknown":
        if history.get("current_row_count") != "unknown":
            ctx.fail(f"{name}: history_unknown must not carry a current row count")
        return
    current = history.get("current_readback") or {}
    receipt = ctx.receipts.get(str(current.get("receipt")))
    data = (receipt or {}).get("data_readback") or {}
    count = history.get("current_row_count")
    if not (
        data.get("query_executed") is True
        and isinstance(count, int)
        and _same(data.get("current_row_count"), count)
        and data.get("model") == name
        and data.get("relation")
        and data.get("relation") == current.get("relation")
    ):
        ctx.fail(f"{name}: history_current needs a scoped DB readback receipt with this count")
        return
    ctx.not_after_capture(f"{name} history readback", data.get("observed_at"))


def _check_model(ctx: _Context, model: Mapping[str, Any]) -> None:
    name = str(model.get("model"))
    registry = model.get("registry") or {}
    artifact = model.get("artifact") or {}
    approval = model.get("approval") or {}
    _check_registry(ctx, name, registry)
    _check_history(ctx, name, model.get("history") or {})

    if artifact.get("state") not in ARTIFACT_STATES:
        ctx.fail(f"{name}: unknown artifact state {artifact.get('state')!r}")
    elif artifact.get("state") == "recovered":
        ctx.fail(f"{name}: artifact state recovered is unattested: {UNATTESTABLE}")
    elif artifact.get("state") == "artifact_missing":
        _check_scoped(ctx, name, "artifact_missing", artifact)

    if approval.get("state") not in APPROVAL_STATES:
        ctx.fail(f"{name}: unknown approval state {approval.get('state')!r}")
    elif approval.get("state") == "approved":
        ctx.fail(f"{name}: approval state approved is unattested: {UNATTESTABLE}")
    elif approval.get("state") == "approval_missing":
        _check_scoped(ctx, name, "approval_missing", approval)
    elif approval.get("historical_source") not in ctx.sources:
        ctx.fail(f"{name}: approval not_read_current needs a verified historical source")

    if model.get("recovered") is not False:
        ctx.fail(f"{name}: recovered must be false: {UNATTESTABLE}")


def _check_training_requirement(ctx: _Context, item: Mapping[str, Any]) -> None:
    """A cited day span must keep window coverage apart from training readiness."""
    label = f"missing input {item.get('id')}"
    req = item.get("training_requirement")
    if not isinstance(req, Mapping):
        if DAY_SPAN.search(str(item.get("gap", ""))):
            ctx.fail(f"{label}: cites a day span without a bound training_requirement")
        return
    texts = []
    for binding in req.get("evidence") or []:
        if ctx.bind(f"{label} training_requirement", binding) is not None:
            texts.append(ctx.sources[binding["source"]][1].decode("utf-8"))
    text = "\n".join(texts)

    priors = req.get("prior_days_required")
    priors_attestation = req.get("prior_days_attestation_required")
    cov_dates = req.get("window_coverage_eligible_dates")
    cov_present = req.get("window_coverage_present_days")
    floor_dates = req.get("training_floor_eligible_dates")
    floor_present = req.get("training_floor_present_days")
    holdout = req.get("holdout_fraction")
    segment = req.get("minimum_segment_rows")

    needles = {
        "window coverage": f"so {cov_present} attested" if _number(cov_present) else None,
        "training floor span": f"{floor_dates} eligible dates = {floor_present} contiguous attested days"
        if _number(floor_dates) and _number(floor_present)
        else None,
        "holdout fraction": f"holdout_fraction={holdout:.2f}," if _number(holdout) else None,
        "segment holdout rows": f"minimum_segment_rows={segment},",
        "priors not attested": "bottom 28 days of any span are priors and never need to be attested",
        "priors ingested": "date becomes eligible when its priors are *ingested*, not when they settle.",
        "settled state agreement": "agree in the end\nstate, where every ingested day is also attested",
    }
    for what, needle in needles.items():
        if needle is None or needle not in text:
            ctx.fail(f"{label}: {what} is not stated by its bound requirement evidence")

    if priors != 28:
        ctx.fail(f"{label}: prior_days_required must be 28")
    if priors_attestation is not False:
        ctx.fail(f"{label}: priors do not require attestation; attestation applies to target dates")
    if not (
        _number(cov_present)
        and _number(floor_present)
        and _number(cov_dates)
        and _number(floor_dates)
        and cov_present < floor_present
        and cov_dates < floor_dates
        and cov_dates == cov_present - priors
        and floor_dates == floor_present - priors
    ):
        ctx.fail(f"{label}: window coverage must stay below the training floor")
    if req.get("floor_is_sufficient") is not False:
        ctx.fail(f"{label}: the training floor is necessary, not sufficient")
    if req.get("current_row_count") != "unknown":
        ctx.fail(f"{label}: current counts are unknown to this investigation")
    gates = set(req.get("remaining_gates") or [])
    if not {"real_data", "lineage", "quality"} <= gates:
        ctx.fail(f"{label}: real-data, lineage and quality gates must remain open")


def _check_original_task(ctx: _Context, task: Mapping[str, Any]) -> dict[str, Any] | None:
    tid = str(task.get("task_id"))
    head = str(task.get("branch_head", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        ctx.fail(f"{tid}: branch_head must be a full SHA")
    for item in task.get("terminal_evidence") or []:
        ref = ctx.bind(f"{tid} terminal evidence", item)
        if ref is not None and not ref.startswith(f"{head}:"):
            ctx.fail(f"{tid}: terminal evidence {ref} is not on branch_head")

    board = task.get("board") or {}
    past = board.get("historical_observation") or {}
    receipt = ctx.receipts.get(str(past.get("receipt"))) or {}
    store = receipt.get("artifact_store_readback") or {}
    if past.get("observed_at") != store.get("checked_at"):
        ctx.fail(f"{tid}: historical board observation time differs from its receipt")
    if (
        past.get("on_board_or_archive") is not False
        or tid not in (store.get("historical_model_tasks") or [])
        or "Neither exact ID found" not in str(store.get("current_board_and_archive", ""))
    ):
        ctx.fail(f"{tid}: historical board absence is not stated by its receipt")

    current = board.get("current") or {}
    snapshot = ctx.receipts.get(str(current.get("receipt")))
    if snapshot is None:
        ctx.fail(f"{tid}: current board state is not a verified saved receipt")
        return None
    entry = next((t for t in snapshot.get("tasks") or [] if t.get("id") == tid), None)
    if entry is None:
        ctx.fail(f"{tid}: current board receipt has no entry for this task")
        return None
    for field in BOARD_FIELDS:
        if not _same(current.get(field), entry.get(field)):
            ctx.fail(f"{tid}: current board {field} differs from the board receipt")
    then, now = _time(past.get("observed_at")), _time(snapshot.get("captured_at"))
    if then is None or now is None or then >= now:
        ctx.fail(f"{tid}: historical board observation must precede the current board readback")
    return entry


def validate(
    doc: Mapping[str, Any],
    *,
    root: Path = REPO_ROOT,
    git_blob: BlobResolver | None = None,
    commit_time: CommitTimeResolver | None = None,
    committed_at: datetime | None = None,
    now: datetime | None = None,
) -> list[str]:
    ctx = _Context(
        doc,
        root,
        git_blob or git_blob_resolver(root),
        commit_time or git_commit_time_resolver(root),
    )
    if doc.get("mutation") is not False or doc.get("cloud_writes"):
        ctx.fail("investigation must not record a cloud write")

    if ctx.captured_at is None:
        ctx.fail(f"captured_at: invalid timestamp {doc.get('captured_at')!r}")
    else:
        if ctx.captured_at > (now or datetime.now(UTC)):
            ctx.fail("captured_at is in the future")
        if committed_at is not None and ctx.captured_at > committed_at:
            ctx.fail("captured_at is after the commit that records it")

    ctx.load_receipts()
    ctx.load_sources()

    for text in _strings(doc):
        if UNBOUNDED_ABSENCE.search(text):
            ctx.fail(f"unbounded absence claim: {text[:80]!r}")

    models = doc.get("models") or []
    names = {m.get("model") for m in models}
    if names != READINESS_MODELS or len(models) != len(READINESS_MODELS):
        ctx.fail(f"models must be exactly the four readiness models, got {sorted(map(str, names))}")
    for model in models:
        _check_model(ctx, model)

    # Neither follows from recovered flags; this investigation can attest neither.
    claims = doc.get("claims") or {}
    if claims.get("model_ready") is not False:
        ctx.fail("model_ready claimed by a bounded investigation")
    if claims.get("historical_state_bytes_recovered") is not False:
        ctx.fail("historical state bytes claimed recovered by a bounded investigation")
    if claims.get("cloud_authority") != "none":
        ctx.fail("an investigation holds no cloud authority")
    for claim in ("live_done", "deploy_go"):
        if claims.get(claim) is not False:
            ctx.fail(f"{claim} cannot be claimed by an investigation")

    risk = doc.get("risk_acceptance") or {}
    ctx.bind("risk_acceptance", risk)
    if risk.get("used_as_current_go") is not False:
        ctx.fail("July risk acceptance cannot be used as a current GO")

    entry_points = doc.get("release_entrypoints") or {}
    if ctx.bind("release_entrypoints", entry_points) is not None:
        text = ctx.sources[entry_points["source"]][1].decode("utf-8")
        has_import = re.search(r"add_parser\(\s*\"import", text) is not None
        if entry_points.get("import_existing_artifact_entrypoint") is not has_import:
            ctx.fail("release_entrypoints import flag contradicts release.py")

    holders: dict[str, dict[str, Any]] = {}
    for task in doc.get("original_tasks") or []:
        entry = _check_original_task(ctx, task)
        if entry is not None:
            holders[str(task.get("task_id"))] = entry

    missing = doc.get("missing_inputs") or []
    if not missing:
        ctx.fail("unrecovered models need missing_inputs")
    for item in missing:
        if not item.get("responsible") or not item.get("handback_task"):
            ctx.fail(f"missing input {item.get('id')} needs responsible and handback_task")
        entry = holders.get(str(item.get("handback_task")))
        if entry is not None:
            holder = item.get("handback_holder") or {}
            if (holder.get("owner"), holder.get("reviewer")) != (
                entry.get("owner"),
                entry.get("reviewer"),
            ):
                ctx.fail(f"missing input {item.get('id')} handback holder differs from board")
        _check_training_requirement(ctx, item)
    if doc.get("operation_plan") is not None:
        ctx.fail("operation plan proposed with nothing recovered")
    return ctx.errors


def main() -> int:
    doc = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    errors = validate(doc, committed_at=evidence_committed_at(REPO_ROOT))
    for error in errors:
        print(f"FAIL: {error}")
    if not errors:
        print("recovery evidence OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
