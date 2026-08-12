#!/usr/bin/env python3
"""Validate, format, and readback-compare redacted Cloud Scheduler trigger snapshots."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


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


def validate_snapshot(payload: dict[str, Any]) -> None:
    if not _exists(payload):
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
        # gcloud accepts a single comma-separated header map for update;
        # repeating --update-headers silently leaves Content-Type at its
        # message-body default (application/octet-stream).
        header_values = ",".join(f"{k}={v}" for k, v in headers.items())
        args.append(f"--headers={header_values}")
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

    retry = payload.get("retryConfig")
    if isinstance(retry, Mapping):
        attempts = retry.get("maxRetryAttempts") or retry.get("retryCount")
        if attempts is not None:
            args.append(f"--max-retry-attempts={attempts}")
        if retry.get("maxRetryDuration"):
            args.append(f"--max-retry-duration={retry['maxRetryDuration']}")
        if retry.get("minBackoffDuration"):
            # gcloud renamed the Scheduler retry flags; the old
            # *-duration spellings are rejected by current Cloud SDKs.
            args.append(f"--min-backoff={retry['minBackoffDuration']}")
        if retry.get("maxBackoffDuration"):
            args.append(f"--max-backoff={retry['maxBackoffDuration']}")
        if retry.get("maxDoublings") is not None:
            args.append(f"--max-doublings={retry['maxDoublings']}")

    return args


def redact_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    if not _exists(payload):
        return {"exists": False}

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

    retry = payload.get("retryConfig")
    if isinstance(retry, Mapping):
        norm_retry: dict[str, Any] = {}
        attempts = retry.get("maxRetryAttempts") or retry.get("retryCount")
        if attempts is not None:
            norm_retry["maxRetryAttempts"] = int(attempts)
        for key in ("maxRetryDuration", "minBackoffDuration", "maxBackoffDuration"):
            if retry.get(key):
                norm_retry[key] = str(retry[key])
        if retry.get("maxDoublings") is not None:
            norm_retry["maxDoublings"] = int(retry["maxDoublings"])
        norm["retryConfig"] = norm_retry

    return norm


def compare_snapshots(before_payload: dict[str, Any], after_payload: dict[str, Any]) -> bool:
    norm_before = redact_snapshot(before_payload)
    norm_after = redact_snapshot(after_payload)
    return norm_before == norm_after


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("validate", "write-absent", "exists", "field", "restore-args", "redact", "compare"),
    )
    parser.add_argument("--description", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--field")
    parser.add_argument("--location", default="")
    parser.add_argument("--project", default="")
    args = parser.parse_args()

    if args.command == "write-absent":
        if not args.description:
            raise ValueError("--description is required for write-absent")
        args.description.write_text('{"exists": false}\n', encoding="utf-8")
        return 0

    if args.command == "compare":
        if not args.before or not args.after:
            raise ValueError("--before and --after are required for compare")
        try:
            before_data = _load(args.before)
            after_data = _load(args.after)
        except Exception as exc:
            print(f"Readback comparison failed: unparseable snapshot file ({exc})", file=sys.stderr)
            return 1

        if compare_snapshots(before_data, after_data):
            print("Readback equality verified.")
            return 0
        else:
            diff_before = redact_snapshot(before_data)
            diff_after = redact_snapshot(after_data)
            print("Readback configuration drift detected!", file=sys.stderr)
            print(f"Pre-deploy snapshot (redacted): {json.dumps(diff_before, indent=2)}", file=sys.stderr)
            print(f"Post-rollback readback (redacted): {json.dumps(diff_after, indent=2)}", file=sys.stderr)
            return 1

    if not args.description:
        raise ValueError("--description is required")

    payload = _load(args.description)
    if args.command == "exists":
        print("true" if _exists(payload) else "false")
        return 0
    if not _exists(payload):
        if args.command in ("validate", "field", "restore-args", "redact"):
            raise ValueError("scheduler trigger did not exist in the snapshot")

    if args.command == "validate":
        validate_snapshot(payload)
        return 0

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
