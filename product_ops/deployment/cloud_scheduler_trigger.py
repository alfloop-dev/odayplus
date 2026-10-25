#!/usr/bin/env python3
"""Validate, format, and readback-compare redacted Cloud Scheduler trigger snapshots."""

from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.release_receipts import redact_secrets  # noqa: E402


def validate_trigger_state(state: str) -> str:
    if state not in ("ENABLED", "PAUSED"):
        raise ValueError("scheduler state must be ENABLED or PAUSED")
    return state


def _load(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"scheduler snapshot file {path} is empty")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("scheduler snapshot must be a JSON object")
    return payload


def _exists(payload: Mapping[str, Any]) -> bool:
    return payload.get("exists") is not False


def _get_auth_info(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    """Returns (auth_type, service_account_email, extra_auth_val)

    auth_type is 'oidc' or 'oauth'.
    For 'oidc', extra_auth_val is audience.
    For 'oauth', extra_auth_val is scope.
    """
    http_target = payload.get("httpTarget")
    if not isinstance(http_target, Mapping):
        raise ValueError("scheduler snapshot is missing httpTarget")

    if "oidcToken" in http_target and "oauthToken" in http_target:
        raise ValueError("scheduler snapshot has ambiguous authentication")
    if "oidcToken" in http_target and isinstance(http_target["oidcToken"], Mapping):
        token = http_target["oidcToken"]
        email = str(token.get("serviceAccountEmail", "")).strip()
        if not email:
            raise ValueError("scheduler snapshot is missing httpTarget.oidcToken.serviceAccountEmail")
        audience = str(token.get("audience", "")).strip()
        return ("oidc", email, audience)

    if "oauthToken" in http_target and isinstance(http_target["oauthToken"], Mapping):
        token = http_target["oauthToken"]
        email = str(token.get("serviceAccountEmail", "")).strip()
        if not email:
            raise ValueError("scheduler snapshot is missing httpTarget.oauthToken.serviceAccountEmail")
        scope = str(token.get("scope", "")).strip()
        return ("oauth", email, scope)

    raise ValueError("scheduler snapshot is missing auth token (oidcToken or oauthToken)")


def _job_target(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    """Parse only the Cloud Run v2 :run URI used by the release entrypoints."""
    uri = payload.get("httpTarget", {}).get("uri", "")
    match = re.fullmatch(
        r"https://run\.googleapis\.com/v2/projects/([^/]+)/locations/([^/]+)/jobs/([a-z][a-z0-9-]{0,62}):run", str(uri)
    )
    if match is None:
        raise ValueError("scheduler target must be an exact Cloud Run :run Job URI")
    return match.group(1), match.group(2), match.group(3)


def validate_snapshot(
    payload: dict[str, Any], *, trigger: str | None = None,
    project: str | None = None, location: str | None = None,
    identity_only: bool = False,
) -> None:
    contextual = any(value is not None for value in (trigger, project, location))
    if contextual:
        if not all((trigger, project, location)):
            raise ValueError("trigger, project and location are all required for scoped snapshot validation")
        expected_name = f"projects/{project}/locations/{location}/jobs/{trigger}"
        if payload.get("name") != expected_name:
            raise ValueError("scheduler snapshot belongs to another trigger/project/location")
    if not _exists(payload):
        return
    validate_trigger_state(payload.get("state"))
    if identity_only:
        if not contextual:
            raise ValueError("identity-only reads require an explicit trigger/project/location")
        return
    for field in ("schedule", "timeZone"):
        val = payload.get(field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"scheduler snapshot field {field} must be non-empty string")

    http_target = payload.get("httpTarget")
    if not isinstance(http_target, Mapping):
        raise ValueError("scheduler snapshot is missing httpTarget")

    uri = http_target.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        raise ValueError("scheduler snapshot field httpTarget.uri must be non-empty string")

    _get_auth_info(payload)
    target = None
    if contextual or "jobBinding" in payload:
        target = _job_target(payload)
        name = re.fullmatch(r"projects/([^/]+)/locations/([^/]+)/jobs/([^/]+)", str(payload.get("name", "")))
        if name is None or target[:2] != (name.group(1), name.group(2)):
            raise ValueError("scheduler target and trigger must share the exact project/location")
    if "jobBinding" in payload:
        binding = payload["jobBinding"]
        if (not isinstance(binding, Mapping) or not binding.get("job_name")
                or not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", str(binding.get("job_image", "")))
                or not re.fullmatch(r"[0-9a-f]{40}", str(binding.get("release_sha", "")))):
            raise ValueError("scheduler snapshot has an invalid immutable job binding")
        assert target is not None
        target_project, target_location, target_job = target
        resource = f"projects/{target_project}/locations/{target_location}/jobs/{target_job}"
        if binding["job_name"] not in (target_job, resource):
            raise ValueError("scheduler snapshot Job binding does not match its exact :run URI")


def _value(payload: Mapping[str, Any], field: str) -> str:
    current: object = payload
    parts = field.split(".")
    for part in parts:
        if isinstance(current, Mapping) and part in current:
            current = current[part]

        else:
            if field in (
                "httpTarget.oauthToken.serviceAccountEmail",
                "httpTarget.oidcToken.serviceAccountEmail",
            ):
                try:
                    _, sa_email, _ = _get_auth_info(payload)
                    return sa_email
                except Exception:
                    pass
            if field == "httpTarget.oauthToken.scope":
                try:
                    auth_type, _, extra = _get_auth_info(payload)
                    return extra if auth_type == "oauth" else ""
                except Exception:
                    pass
            if field == "httpTarget.oidcToken.audience":
                try:
                    auth_type, _, extra = _get_auth_info(payload)
                    return extra if auth_type == "oidc" else ""
                except Exception:
                    pass
            raise ValueError(f"scheduler snapshot is missing {field}")

    if not isinstance(current, str) or not current.strip():
        raise ValueError(f"scheduler snapshot field {field} must be non-empty")
    return current.strip()


def decode_body(body_val: Any) -> str:
    if not body_val or not isinstance(body_val, str):
        return ""
    try:
        raw = base64.b64decode(body_val, validate=True)
        return raw.decode("utf-8")
    except Exception:
        return body_val


def generate_restore_args(payload: dict[str, Any], location: str, project: str) -> list[str]:
    validate_snapshot(payload)
    if not _exists(payload):
        raise ValueError("cannot generate restore args for absent trigger snapshot")

    args: list[str] = [
        f"--location={location}",
        f"--project={project}",
        f"--schedule={payload['schedule']}",
        f"--time-zone={payload['timeZone']}",
    ]
    args.append(f"--attempt-deadline={payload.get('attemptDeadline', '180s')}")
    args.append(f"--description={payload.get('description', '')}")

    http_target = payload.get("httpTarget", {})
    uri = http_target.get("uri", "")
    args.append(f"--uri={uri}")

    method = http_target.get("httpMethod", "POST").upper()
    args.append(f"--http-method={method}")

    raw_body = http_target.get("body", "")
    body_text = decode_body(raw_body)
    if not body_text and method == "POST":
        body_text = "{}"
    if body_text:
        args.append(f"--message-body={body_text}")

    headers = http_target.get("headers")
    if isinstance(headers, Mapping) and headers:
        # gcloud parses --headers/--update-headers as a single dict flag, so a
        # repeated flag keeps only the last pair.  Emitting one comma-separated
        # map preserves every header; without it Content-Type is dropped and
        # falls back to the message-body default (application/octet-stream),
        # which the post-restore readback then reports as drift.
        args.append("--headers=" + ",".join(f"{k}={v}" for k, v in sorted(headers.items())))
    elif method == "POST":
        args.append("--headers=Content-Type=application/json")

    auth_type, sa_email, extra = _get_auth_info(payload)
    if auth_type == "oidc":
        args.append(f"--oidc-service-account-email={sa_email}")
        if extra:
            args.append(f"--oidc-token-audience={extra}")
    elif auth_type == "oauth":
        args.append(f"--oauth-service-account-email={sa_email}")
        if extra:
            args.append(f"--oauth-token-scope={extra}")

    retry = _normalized_retry(payload)
    args.extend((f"--max-retry-attempts={retry['maxRetryAttempts']}",
                 f"--max-retry-duration={retry['maxRetryDuration']}",
                 f"--min-backoff={retry['minBackoffDuration']}",
                 f"--max-backoff={retry['maxBackoffDuration']}",
                 f"--max-doublings={retry['maxDoublings']}"))

    return args


def _normalized_retry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Scheduler omits proto default values in describe; preserve their meaning."""
    retry = payload.get("retryConfig", {})
    if not isinstance(retry, Mapping):
        raise ValueError("retryConfig must be an object")
    if "maxRetryAttempts" in retry and "retryCount" in retry and retry["maxRetryAttempts"] != retry["retryCount"]:
        raise ValueError("retry count aliases disagree")
    return {
        "maxRetryAttempts": int(retry.get("maxRetryAttempts", retry.get("retryCount", 0))),
        "maxRetryDuration": str(retry.get("maxRetryDuration", "0s")),
        "minBackoffDuration": str(retry.get("minBackoffDuration", "5s")),
        "maxBackoffDuration": str(retry.get("maxBackoffDuration", "3600s")),
        "maxDoublings": int(retry.get("maxDoublings", 5)),
    }


def redact_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize configuration for equality; this does not remove secrets."""
    if not _exists(payload):
        return {"exists": False, **({"name": payload["name"]} if "name" in payload else {})}

    http_target = payload.get("httpTarget", {})
    auth_type, sa_email, extra = _get_auth_info(payload)

    auth_dict: dict[str, str] = {"type": auth_type, "serviceAccountEmail": sa_email}
    if auth_type == "oidc" and extra:
        auth_dict["audience"] = extra
    elif auth_type == "oauth" and extra:
        auth_dict["scope"] = extra

    headers = http_target.get("headers", {})
    norm_headers = {k: str(v) for k, v in sorted(headers.items())} if isinstance(headers, Mapping) else {}
    if not norm_headers and http_target.get("httpMethod", "POST").upper() == "POST":
        norm_headers = {"Content-Type": "application/json"}

    raw_body = http_target.get("body", "")
    body_text = decode_body(raw_body)
    if not body_text and http_target.get("httpMethod", "POST").upper() == "POST":
        body_text = "{}"

    norm: dict[str, Any] = {
        "exists": True,
        "schedule": payload.get("schedule", ""),
        "timeZone": payload.get("timeZone", ""),
        "state": payload.get("state", "ENABLED"),
        "httpTarget": {
            "uri": http_target.get("uri", ""),
            "httpMethod": http_target.get("httpMethod", "POST").upper(),
            "headers": norm_headers,
            "body": body_text,
            "auth": auth_dict,
        },
    }
    if "name" in payload:
        norm["name"] = payload["name"]

    norm["retryConfig"] = _normalized_retry(payload)
    norm["attemptDeadline"] = payload.get("attemptDeadline", "180s")
    norm["description"] = payload.get("description", "")

    return norm


def compare_snapshots(
    before_payload: dict[str, Any], after_payload: dict[str, Any], *, expected_state: str | None = None,
) -> bool:
    validate_snapshot(before_payload)
    validate_snapshot(after_payload)
    norm_before = redact_snapshot(before_payload)
    if expected_state is not None:
        norm_before["state"] = validate_trigger_state(expected_state)
    norm_after = redact_snapshot(after_payload)
    return norm_before == norm_after


def verified_resume_state(candidate: dict[str, Any], actual: dict[str, Any], desired: str) -> str:
    """Accept only paused preparation or the exact already-completed desired state."""
    validate_trigger_state(desired)
    current = actual.get("state")
    if current not in {"PAUSED", desired} or not compare_snapshots(candidate, actual, expected_state=current):
        raise ValueError("scheduler resume readback configuration/state drift detected")
    return str(current)


def recovery_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep fully restorable configuration; this is restricted recovery data."""
    validate_snapshot(payload)
    keys = ("name", "exists", "schedule", "timeZone", "state", "httpTarget", "retryConfig",
            "attemptDeadline", "description", "jobBinding")
    result = {key: copy.deepcopy(payload[key]) for key in keys if key in payload}
    if not _exists(payload):
        return result
    target = result["httpTarget"]
    if set(target) - {"uri", "httpMethod", "headers", "body", "oauthToken", "oidcToken"}:
        raise ValueError("scheduler HTTP target contains fields this recovery contract cannot restore")
    auth_type, _, _ = _get_auth_info(result)
    auth_key = "oauthToken" if auth_type == "oauth" else "oidcToken"
    auth_fields = {"serviceAccountEmail", "scope" if auth_type == "oauth" else "audience"}
    if set(target[auth_key]) - auth_fields:
        raise ValueError("scheduler authentication contains fields unsafe for recovery publication")
    if "jobBinding" in result:
        result["jobBinding"] = {key: result["jobBinding"][key] for key in ("job_name", "job_image", "release_sha")}
    normalized = redact_snapshot(result)
    content = normalized["httpTarget"]
    try:
        content["body"] = json.loads(content["body"])
    except (ValueError, TypeError) as exc:
        raise ValueError("scheduler recovery HTTP body must be a JSON object") from exc
    if not isinstance(content["body"], dict):
        raise ValueError("scheduler recovery HTTP body must be a JSON object")
    safe, _ = redact_secrets(normalized)
    if safe != normalized:
        raise ValueError("scheduler recovery configuration contains sensitive values; refusing artifact publication")
    return result


def candidate_snapshot(
    before: dict[str, Any], *, trigger: str, project: str, location: str, job: str,
    cron: str, time_zone: str, service_account: str,
) -> dict[str, Any]:
    """The deploy and manual paths share one release-scoped :run target."""
    validate_snapshot(before, trigger=trigger, project=project, location=location)
    if not all((project, location, cron, time_zone, service_account)) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", job):
        raise ValueError("complete scheduler settings and exact job name are required")
    candidate = copy.deepcopy(before) if _exists(before) else {}
    candidate.pop("exists", None)
    candidate.pop("jobBinding", None)
    candidate["name"] = f"projects/{project}/locations/{location}/jobs/{trigger}"
    candidate.update(schedule=cron, timeZone=time_zone, state="PAUSED")
    if not _exists(before):
        candidate["attemptDeadline"] = "180s"
        candidate["retryConfig"] = {
            "retryCount": 0, "maxRetryDuration": "0s", "minBackoffDuration": "5s",
            "maxBackoffDuration": "3600s", "maxDoublings": 5,
        }
    headers = dict(candidate.get("httpTarget", {}).get("headers", {}))
    headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
    headers["Content-Type"] = "application/json"
    candidate["httpTarget"] = {
        "uri": f"https://run.googleapis.com/v2/projects/{project}/locations/{location}/jobs/{job}:run",
        "httpMethod": "POST", "body": "e30=", "headers": headers,
        "oauthToken": {"serviceAccountEmail": service_account, "scope": "https://www.googleapis.com/auth/cloud-platform"},
    }
    validate_snapshot(candidate, trigger=trigger, project=project, location=location)
    return candidate


def validate_job_receipt(receipt: dict[str, Any], *, release: str, job: str, image: str, kind: str) -> None:
    """Consume the existing jobs-smoke report, including its immutable binding."""
    expected_job = job.rsplit("/", 1)[-1]
    if receipt.get("ok") is not True or receipt.get("expected_sha") != release or receipt.get("job_kind") != kind:
        raise ValueError("a passing jobs-smoke receipt for the same release and kind is required")
    if receipt.get("job_name") not in (job, expected_job) or receipt.get("execution_job_name") not in (job, expected_job):
        raise ValueError("jobs-smoke receipt job binding does not match")
    if receipt.get("job_image") != image or receipt.get("execution_image") != image or not receipt.get("execution_name"):
        raise ValueError("jobs-smoke receipt lacks the exact immutable job/execution image binding; regenerate old receipts")
    execution = str(receipt["execution_name"])
    local_execution = execution.removeprefix(job + "/executions/")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", local_execution):
        raise ValueError("jobs-smoke execution must belong to this exact Job/project/location")
    checks = receipt.get("checks")
    required = {f"jobs-smoke:{kind}:{name}" for name in ("release_sha", "entrypoint", "secret_bindings", "execution", "execution_receipt", "immutable_binding")}
    if not isinstance(checks, list) or not checks or any(not isinstance(check, dict) or check.get("ok") is not True for check in checks) or not required.issubset({check.get("name") for check in checks}):
        raise ValueError("jobs-smoke receipt is missing successful verification checks")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("validate", "write-absent", "exists", "field", "restore-args", "redact", "compare", "candidate", "receipt", "target-job", "attach-binding", "first-state", "resume-state", "recovery"),
    )
    parser.add_argument("--description", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--field")
    parser.add_argument("--location", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--trigger")
    parser.add_argument("--job", default="")
    parser.add_argument("--cron", default="")
    parser.add_argument("--time-zone", default="")
    parser.add_argument("--service-account", default="")
    parser.add_argument("--release", default="")
    parser.add_argument("--image", default="")
    parser.add_argument("--kind", choices=("worker", "scheduler"))
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--state")
    parser.add_argument("--identity-only", action="store_true")
    args = parser.parse_args()

    if args.command == "first-state":
        print(validate_trigger_state(args.state))
        return 0

    if args.command == "write-absent":
        if not args.description:
            raise ValueError("--description is required for write-absent")
        payload = {"exists": False}
        if args.trigger:
            payload["name"] = f"projects/{args.project}/locations/{args.location}/jobs/{args.trigger}"
        args.description.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return 0

    if args.command in ("compare", "resume-state"):
        if not args.before or not args.after:
            raise ValueError("--before and --after are required for compare")
        try:
            before_data = _load(args.before)
            after_data = _load(args.after)
        except Exception as exc:
            print(f"Readback comparison failed: unparseable snapshot file ({exc})", file=sys.stderr)
            return 1

        if args.command == "resume-state":
            print(verified_resume_state(before_data, after_data, args.state))
            return 0
        if compare_snapshots(before_data, after_data, expected_state=args.state):
            print("Readback equality verified.")
            return 0
        else:
            print("Readback configuration drift detected!", file=sys.stderr)
            return 1

    if not args.description:
        raise ValueError("--description is required")

    payload = _load(args.description)
    if args.command == "recovery":
        args.description.write_text(json.dumps(recovery_snapshot(payload)) + "\n", encoding="utf-8")
        return 0
    if args.command == "target-job":
        validate_snapshot(payload, trigger=args.trigger, project=args.project, location=args.location)
        print(_job_target(payload)[2])
        return 0
    if args.command == "attach-binding":
        if args.binding is None:
            raise ValueError("--binding is required")
        payload["jobBinding"] = _load(args.binding)
        validate_snapshot(payload)
        args.description.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return 0
    if args.command == "candidate":
        print(json.dumps(candidate_snapshot(payload, trigger=args.trigger, project=args.project, location=args.location, job=args.job,
                                            cron=args.cron, time_zone=args.time_zone, service_account=args.service_account)))
        return 0
    if args.command == "receipt":
        validate_job_receipt(payload, release=args.release, job=args.job, image=args.image, kind=args.kind)
        return 0
    if args.command == "exists":
        print("true" if _exists(payload) else "false")
        return 0
    if args.command == "validate":
        validate_snapshot(payload, trigger=args.trigger, project=args.project or None, location=args.location or None,
                          identity_only=args.identity_only)
        return 0
    if not _exists(payload):
        if args.command in ("field", "restore-args", "redact"):
            raise ValueError("scheduler trigger did not exist in the snapshot")

    if args.command == "field":
        if not args.field:
            raise ValueError("--field is required for field")
        print(_value(payload, args.field))
        return 0

    if args.command == "restore-args":
        gargs = generate_restore_args(payload, args.location, args.project)
        sys.stdout.buffer.write(("\0".join(gargs) + "\0").encode("utf-8"))
        return 0

    if args.command == "redact":
        redacted = redact_snapshot(payload)
        print(json.dumps(redacted, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
