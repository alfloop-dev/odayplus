#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

try:  # PyYAML is optional; yaml_dump falls back to JSON without it.
    import yaml
except ImportError:  # pragma: no cover - exercised only without PyYAML
    yaml = None

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
TASK_BRIEFS_DIR = ORCHESTRATOR_DIR / "task-briefs"
EVIDENCE_DIR = ORCHESTRATOR_DIR / "evidence"
CLOSEOUT_SPEC_PATH = ORCHESTRATOR_DIR / "skills" / "task-closeout-finalization.md"
WORKER_ANCHOR_SPEC_PATH = ORCHESTRATOR_DIR / "skills" / "worker-anchor-commit.md"
SUPERVISOR_SCRIPT_NAME = "supervisor.py"
SUPERVISOR_SCRIPT_REL = f".orchestrator/{SUPERVISOR_SCRIPT_NAME}"
SUPERVISOR_SCRIPT_PATH = ORCHESTRATOR_DIR / SUPERVISOR_SCRIPT_NAME
DEFAULT_CONFIG_PATH = ORCHESTRATOR_DIR / "config.json"
LOCAL_CONFIG_PATH = ORCHESTRATOR_DIR / "config.local.json"
CONFIG_SCHEMA_PATH = ORCHESTRATOR_DIR / "config.schema.json"
# The orchestrator was ported from a project called Pantheon and its environment
# contract still carries that name. Workers are running with the old variables
# set right now, and a worker's environment is fixed when it is spawned, so the
# old names cannot simply be replaced -- they are read here for as long as any
# process started under them can still be alive. New spawns get both.
CONFIG_PATH_ENV_VAR = "ORCH_CONFIG_PATH"
LEGACY_CONFIG_PATH_ENV_VAR = "PANTHEON_CONFIG_PATH"
STATUS_ROOT_ENV_VAR = "ORCH_STATUS_ROOT"
LEGACY_STATUS_ROOT_ENV_VAR = "PANTHEON_STATUS_ROOT"
PLANNING_STATE_PATH = ORCHESTRATOR_DIR / "planning-state.json"
DEFAULT_PLANNING_SHARED_FILES = [
    ROOT / "docs" / "02-architecture" / "consensus" / "phase1" / "README.md",
    ROOT / "docs" / "02-architecture" / "consensus" / "phase1" / "planning-session.json",
    ROOT / "docs" / "02-architecture" / "consensus" / "phase1" / "pantheon-backend-completion-checklist.md",
]
CLAUDE_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_OAUTH_SCOPES = (
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
)
CLAUDE_OAUTH_REFRESH_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://claude.ai",
    "Referer": "https://claude.ai/",
    "User-Agent": "claude-code/2.1.117",
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc_timestamp(value: Any) -> datetime | None:
    """Parse an orchestrator timestamp into an aware UTC datetime.

    :func:`parse_iso_timestamp` returns whatever the string carried, so a naive
    document reads back naive and comparing it against ``datetime.now(UTC)``
    raises. Callers that do arithmetic want one timezone: treat a naive stamp
    as UTC (every writer here emits ``Z``) and convert an offset stamp to UTC.
    """
    parsed = parse_iso_timestamp(value)
    if parsed is None:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def isoformat_utc(value: datetime) -> str:
    """Render ``value`` the way :func:`utc_now` renders the current time."""
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_text_if_changed(path: Path, content: str) -> bool:
    """Write ``content`` only when it differs; return whether it was written."""
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if existing == content:
        return False
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")
    return True


def yaml_dump(payload: dict[str, Any]) -> str:
    """Serialize ``payload`` as YAML, falling back to JSON without PyYAML."""
    if yaml is not None:
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def strip_json_comments(text: str) -> str:
    """Remove `//` and `/* */` comments that sit outside string literals.

    A plain ``re.sub(r"//.*?$", ...)`` also eats the second half of every URL
    in the document, turning `"https://github.com/x"` into an unterminated
    `"https:` and reporting the resulting stray newline as an "Invalid control
    character" hundreds of lines away from the real defect. Scanning for string
    boundaries keeps the tolerance for commented config without inventing a
    corruption that was never in the file.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline == -1 else newline
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close == -1 else close + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return deepcopy(default)
    last_error: json.JSONDecodeError | None = None
    for attempt in range(10):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return deepcopy(default)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # Report the error from the file as written. A sanitizer failure
            # describes a document nobody has on disk, which sends whoever is
            # reading the traceback after the wrong defect.
            last_error = exc
            sanitized = strip_json_comments(text)
            sanitized = re.sub(r",(\s*[}\]])", r"\1", sanitized)
            if sanitized != text:
                try:
                    return json.loads(sanitized)
                except json.JSONDecodeError:
                    pass
            if attempt < 9:
                time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return deepcopy(default)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    last_error: json.JSONDecodeError | None = None
    for attempt in range(10):
        rows: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
            return rows
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt < 9:
                time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return []


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def deep_merge(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = deepcopy(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        return deepcopy(overlay)
    return deepcopy(overlay)


class ConfigError(RuntimeError):
    """The orchestrator configuration is missing, malformed or outside its contract."""


# These settings belonged to retired control paths. Strip them before schema
# validation so a deployed, gitignored config.json from the prior release does
# not prevent the Supervisor from starting; their behavior is not retained.
RETIRED_CONFIG_KEYS: frozenset[str] = frozenset({"worker_tree_guard"})


def retire_config_keys(config: Any) -> Any:
    if not isinstance(config, dict):
        return config
    return {key: value for key, value in config.items() if key not in RETIRED_CONFIG_KEYS}


@lru_cache(maxsize=1)
def config_validator() -> Draft202012Validator:
    try:
        schema = json.loads(CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Unable to load config schema {CONFIG_SCHEMA_PATH}: {exc}") from exc
    return Draft202012Validator(schema)


def validate_config(config: Any, *, source: str | Path) -> dict[str, Any]:
    """Validate one base, overlay or merged config and return it unchanged."""
    if not isinstance(config, dict):
        raise ConfigError(f"Orchestrator config {source} must contain a JSON object")
    errors = sorted(
        config_validator().iter_errors(config),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        details: list[str] = []
        for error in errors[:10]:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            details.append(f"{location}: {error.message}")
        if len(errors) > 10:
            details.append(f"... and {len(errors) - 10} more validation errors")
        raise ConfigError(f"Invalid orchestrator config {source}: " + "; ".join(details))
    return config


def load_config_document(path: Path) -> dict[str, Any]:
    """Read and validate a single config document without any fallback source."""
    if not path.is_file():
        raise ConfigError(
            f"Orchestrator config does not exist: {path}. "
            "Run `make bootstrap` for a development checkout or pass --config explicitly."
        )
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ConfigError(f"Orchestrator config is empty: {path}")
        payload = json.loads(text)
    except ConfigError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Unable to parse orchestrator config {path}: {exc}") from exc
    return validate_config(retire_config_keys(payload), source=path)


def resolve_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def relpath(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def evidence_dir(config: dict[str, Any]) -> Path:
    configured = config.get("paths", {}).get("evidence_dir")
    path = resolve_path(configured) if configured else EVIDENCE_DIR
    if path is None:
        return EVIDENCE_DIR
    return path


def load_config(
    config_path: str | Path | None = None,
    *,
    overlay_paths: list[str | Path] | tuple[str | Path, ...] | None = None,
) -> dict[str, Any]:
    """Load the one authoritative config plus explicit local overlays.

    The tracked example is bootstrap input only and is never a runtime
    fallback. The repository's default config gets its sibling local overlay;
    an explicitly selected external config is self-contained unless callers
    explicitly provide overlay paths.
    """
    selected_path = config_path
    if selected_path is None:
        selected_path = str(
            os.environ.get(CONFIG_PATH_ENV_VAR)
            or os.environ.get(LEGACY_CONFIG_PATH_ENV_VAR)
            or ""
        ).strip() or None
    config_file = resolve_path(selected_path) if selected_path else DEFAULT_CONFIG_PATH
    if config_file is None:
        raise RuntimeError("Unable to resolve orchestrator config path")
    config = load_config_document(config_file)
    selected_overlays: tuple[str | Path, ...]
    if overlay_paths is not None:
        selected_overlays = tuple(overlay_paths)
    elif config_file == DEFAULT_CONFIG_PATH:
        selected_overlays = (LOCAL_CONFIG_PATH,)
    else:
        selected_overlays = ()
    applied = [config_file]
    for raw_overlay_path in selected_overlays:
        overlay_path = resolve_path(raw_overlay_path)
        if overlay_path is None or not overlay_path.exists():
            continue
        config = deep_merge(config, load_config_document(overlay_path))
        applied.append(overlay_path)
    return validate_config(config, source=" + ".join(str(path) for path in applied))


def config_path(config: dict[str, Any], key: str, default: str | None = None) -> Path:
    if key == "status_file" and default is None:
        default = str(ROOT / "ai-status.json")
    value = config.get("paths", {}).get(key, default)
    path = resolve_path(value)
    if path is None:
        raise KeyError(f"Missing config path for {key}")
    return path


def repo_root_for_config(config: dict[str, Any]) -> Path:
    return config_path(config, "status_file").parents[0]


def authoritative_status_root(env: Mapping[str, str] | None = None) -> Path | None:
    """Resolve the status root the orchestrator declared for the current process.

    A hook executable does not have to live in the checkout whose approval
    queue is authoritative: the Claude hook wiring pins one absolute
    ``permission_broker.py`` path, while ``PANTHEON_STATUS_ROOT`` names the
    fleet that actually owns the worker, its queue, and its permission rules.
    Module-level ``ROOT`` is derived from ``__file__`` and therefore answers
    "which copy of the code am I", not "which fleet am I acting for".

    Resolution fails closed. An unset, blank, relative, missing, or
    non-orchestrator value returns ``None`` so callers keep their existing
    ``ROOT``-relative behaviour instead of guessing at another root.
    """
    source = env if env is not None else os.environ
    raw = str(
        source.get(STATUS_ROOT_ENV_VAR) or source.get(LEGACY_STATUS_ROOT_ENV_VAR) or ""
    ).strip()
    if not raw:
        return None
    candidate = Path(os.path.expanduser(raw))
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    if not (resolved / ".orchestrator" / "config.json").is_file():
        return None
    return resolved


def anchor_config_paths(config: dict[str, Any], root: Path) -> dict[str, Any]:
    """Return a copy of ``config`` whose relative ``paths`` resolve under ``root``.

    ``resolve_path`` anchors relative values to the module-level ``ROOT``, so a
    config carried across checkouts silently points at the wrong state files.
    Absolute entries are left untouched: those are explicit operator overrides.
    """
    anchored = deepcopy(config)
    paths = anchored.get("paths")
    if not isinstance(paths, dict):
        return anchored
    for key, value in list(paths.items()):
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        candidate = Path(os.path.expanduser(text))
        if candidate.is_absolute():
            continue
        paths[key] = str(root / candidate)
    return anchored


def load_config_for_status_root(root: Path) -> dict[str, Any]:
    """Load ``root``'s orchestrator config with every relative path anchored to it."""
    selected_path = str(os.environ.get(CONFIG_PATH_ENV_VAR) or "").strip()
    if selected_path:
        config = load_config(selected_path)
    else:
        local_path = root / ".orchestrator" / "config.local.json"
        config = load_config(
            root / ".orchestrator" / "config.json",
            overlay_paths=(local_path,),
        )
    return anchor_config_paths(config, root)


def _expand_workspace_path(value: Any, *, base: Path) -> Path:
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def delivery_workspace_root(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> Path:
    repo_root = repo_root_for_config(config)
    raw_path = (metadata or {}).get("workspace_path")
    if raw_path:
        return _expand_workspace_path(raw_path, base=repo_root)
    return repo_root


def delivery_status_root(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> Path:
    repo_root = repo_root_for_config(config)
    raw_path = (metadata or {}).get("status_root")
    if raw_path:
        return _expand_workspace_path(raw_path, base=repo_root)
    return repo_root


def delivery_runtime_env(config: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, str]:
    workspace_root = delivery_workspace_root(config, metadata)
    status_root = delivery_status_root(config, metadata)
    result = {
        "ORCH_WORKTREE_ROOT": str(workspace_root),
        "ORCH_STATUS_ROOT": str(status_root),
        "ORCH_WORKSPACE_PATH": str(workspace_root),
        # The Pantheon-prefixed names are the orchestrator's original contract,
        # from the project this was ported from. Every worker prompt, task
        # script and skill document in the tree still names them, so they are
        # set alongside the new ones rather than replaced. Removing them is a
        # separate change that has to update those callers first.
        "PANTHEON_WORKTREE_ROOT": str(workspace_root),
        "PANTHEON_STATUS_ROOT": str(status_root),
    }
    config_path = str(
        os.environ.get(CONFIG_PATH_ENV_VAR)
        or os.environ.get(LEGACY_CONFIG_PATH_ENV_VAR)
        or ""
    ).strip()
    if config_path:
        result[CONFIG_PATH_ENV_VAR] = config_path
    materialized_context = (metadata or {}).get("materialized_context_files")
    if isinstance(materialized_context, list):
        # task_finalize.sh invokes the shared policy in a separate process, so
        # carry only Supervisor-originated paths explicitly rather than giving
        # it a second path classifier or a broad repository allowlist.
        result["ORCH_MATERIALIZED_CONTEXT_PATHS"] = json.dumps(
            [str(path) for path in materialized_context], ensure_ascii=False
        )
    actor_name = str((metadata or {}).get("target_display_name") or "").strip()
    if actor_name:
        # The live Supervisor has already authorized this dispatch target from
        # its merged fleet config. A task branch can carry a newer tracked
        # config which no longer declares a legacy lane, while its status-root
        # overlay only declares current physical slots. Carry the authorized
        # target into the worker so prompt identity, AI_NAME and official
        # ai-status actor authority cannot drift across branch revisions.
        existing = [
            item.strip()
            for item in str(os.environ.get("AI_STATUS_EXTRA_AGENTS") or "").split(",")
            if item.strip()
        ]
        extras = list(dict.fromkeys([*existing, actor_name]))
        result["AI_NAME"] = actor_name
        result["AI_STATUS_EXTRA_AGENTS"] = ",".join(extras)
    return result


def github_cli_config_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env or os.environ
    configured = str(source.get("GH_CONFIG_DIR") or "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    xdg_config_home = str(source.get("XDG_CONFIG_HOME") or "").strip()
    if xdg_config_home:
        return Path(os.path.expanduser(xdg_config_home)) / "gh"
    home = str(source.get("HOME") or str(Path.home())).strip() or str(Path.home())
    return Path(os.path.expanduser(home)) / ".config" / "gh"


def preserve_github_cli_auth_env(env: dict[str, str], source_env: Mapping[str, str] | None = None) -> None:
    if env.get("GH_CONFIG_DIR"):
        env["GH_CONFIG_DIR"] = os.path.expanduser(str(env["GH_CONFIG_DIR"]))
        return
    config_dir = github_cli_config_dir(source_env)
    if config_dir.exists():
        env["GH_CONFIG_DIR"] = str(config_dir)


def is_github_cli_auth_failure(reason: str | None) -> bool:
    normalized = compact_whitespace(reason).lower()
    if not normalized:
        return False
    markers = (
        "github cli is not authenticated",
        "gh cli is not authenticated",
        "gh is not authenticated",
        "you are not logged into any github hosts",
        "to log in, run: gh auth login",
    )
    return any(marker in normalized for marker in markers)


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        check=check,
        timeout=timeout,
        text=True,
        capture_output=True,
        env=env,
    )


def claude_credentials_path(env: dict[str, str] | None = None) -> Path:
    source = env or os.environ
    configured = str(source.get("CLAUDE_CONFIG_DIR") or "").strip()
    if configured:
        config_dir = Path(os.path.expanduser(configured))
    else:
        home = str(source.get("HOME") or str(Path.home())).strip() or str(Path.home())
        config_dir = Path(os.path.expanduser(home)) / ".claude"
    return config_dir / ".credentials.json"


def load_claude_oauth_tokens(env: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    credentials_path = claude_credentials_path(env)
    payload = load_json(credentials_path, default={}) or {}
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    return payload, oauth, credentials_path


def claude_oauth_token_expired(oauth: dict[str, Any], *, skew_seconds: int = 300) -> bool:
    if not oauth.get("accessToken"):
        return True
    expires_at = oauth.get("expiresAt")
    if expires_at in (None, ""):
        return False
    try:
        expires_at_ms = int(expires_at)
    except (TypeError, ValueError):
        return True
    return expires_at_ms <= int(time.time() * 1000) + (skew_seconds * 1000)


def claude_oauth_token_from_env(env: dict[str, str] | None = None) -> str | None:
    source = env or os.environ
    token = str(source.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    return token if token.startswith("sk-ant-") else None


def apply_claude_oauth_token_file(env: dict[str, str], runtime: dict[str, Any]) -> dict[str, str]:
    if claude_oauth_token_from_env(env):
        return env
    token_file = str(runtime.get("oauth_token_file") or runtime.get("oauth_token_path") or "").strip()
    if not token_file:
        return env
    path = Path(os.path.expanduser(token_file))
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return env
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def refresh_claude_oauth_tokens(env: dict[str, str] | None = None, *, timeout: float = 15.0) -> dict[str, Any] | None:
    loaded = load_claude_oauth_tokens(env)
    if not loaded:
        return None
    payload, oauth, credentials_path = loaded
    refresh_token = str(oauth.get("refreshToken") or "").strip()
    if not refresh_token:
        return None
    request_body = json.dumps(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLAUDE_OAUTH_CLIENT_ID,
            "scope": " ".join(CLAUDE_OAUTH_SCOPES),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        CLAUDE_OAUTH_TOKEN_URL,
        data=request_body,
        headers=CLAUDE_OAUTH_REFRESH_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    updated = deepcopy(oauth)
    updated["accessToken"] = response_payload.get("access_token") or updated.get("accessToken") or ""
    updated["refreshToken"] = response_payload.get("refresh_token") or refresh_token
    expires_in = response_payload.get("expires_in")
    if expires_in is not None:
        try:
            updated["expiresAt"] = int(time.time() * 1000) + (int(expires_in) * 1000)
        except (TypeError, ValueError):
            pass
    scopes = response_payload.get("scope")
    if isinstance(scopes, str) and scopes.strip():
        updated["scopes"] = scopes.split()
    elif not updated.get("scopes"):
        updated["scopes"] = list(CLAUDE_OAUTH_SCOPES)
    payload["claudeAiOauth"] = updated
    write_json(credentials_path, payload)
    return updated


CLAUDE_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def claude_model_selection_args(runtime: dict[str, Any]) -> list[str]:
    """Build the --model/--effort flags for a Claude CLI worker.

    Both are optional. Without them the CLI falls back to the user's
    ~/.claude/settings.json, which is tuned for interactive sessions rather
    than for spec-driven worker runs. Keep every worker on the same model:
    the prompt cache is model-scoped, so mixing models across workers throws
    away the cache the previous run just paid to write.
    """
    args: list[str] = []
    model = str(runtime.get("model") or "").strip()
    if model:
        args.extend(["--model", model])
    effort = str(runtime.get("effort") or "").strip().lower()
    if effort:
        if effort not in CLAUDE_EFFORT_LEVELS:
            raise ValueError(
                f"Unsupported Claude effort level {effort!r}; expected one of {', '.join(CLAUDE_EFFORT_LEVELS)}."
            )
        args.extend(["--effort", effort])
    return args


def claude_auth_ready(binary: str | None, *, env: dict[str, str] | None = None, refresh_if_needed: bool = True) -> bool:
    if not binary:
        return False
    env_token = claude_oauth_token_from_env(env)
    if env_token:
        loaded = load_claude_oauth_tokens(env)
        if not loaded:
            return True
        _, oauth, _ = loaded
        stored_token = str(oauth.get("accessToken") or "").strip()
        if stored_token and stored_token != env_token:
            if not claude_oauth_token_expired(oauth):
                if env is not None:
                    env["CLAUDE_CODE_OAUTH_TOKEN"] = stored_token
            return True
        if stored_token and stored_token == env_token and not claude_oauth_token_expired(oauth):
            return True
        if not refresh_if_needed:
            return False
        refreshed = refresh_claude_oauth_tokens(env)
        if refreshed and not claude_oauth_token_expired(refreshed, skew_seconds=0):
            refreshed_token = str(refreshed.get("accessToken") or "").strip()
            if refreshed_token.startswith("sk-ant-") and env is not None:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = refreshed_token
            return True
        return False
    status = run_command([binary, "auth", "status"], env=env)
    if status.returncode != 0 or not status.stdout:
        return False
    try:
        payload = json.loads(status.stdout)
    except json.JSONDecodeError:
        return False
    if not payload.get("loggedIn"):
        return False
    loaded = load_claude_oauth_tokens(env)
    if not loaded:
        return True
    _, oauth, _ = loaded
    if not claude_oauth_token_expired(oauth):
        return True
    if not refresh_if_needed:
        return False
    refreshed = refresh_claude_oauth_tokens(env)
    return bool(refreshed and not claude_oauth_token_expired(refreshed, skew_seconds=0))


# Every provider wrapper in `.orchestrator/bin/` reports a missing target with
# the same sentence: "Codex CLI binary not found at ...", "Antigravity CLI (agy)
# binary not found under ...", and so on. This is the one output that proves a
# lane is dead rather than merely unhappy, so it is shared by the worker-failure
# classifier and the capability probe instead of being spelled twice.
#
# Deliberately narrow. An earlier version matched any line-initial
# "<token> binary not found", which ordinary task output can produce ("protoc
# binary not found") and which would pause a healthy lane for 900s. Requiring a
# known CLI name *and* the literal "CLI" keeps it to the wrappers' own wording,
# mirroring how AGY_QUOTA_SIGNATURE_PATTERN insists on agy's full signature.
PROVIDER_CLI_NAMES = ("codex", "claude", "antigravity", "copilot", "github", "gemini")
PROVIDER_LAUNCHER_MISSING_PATTERN = re.compile(
    r"^(?P<cli>" + "|".join(PROVIDER_CLI_NAMES) + r")\s+CLI\s*(?:\([^)]*\)\s*)?binary not found\b",
    re.IGNORECASE,
)

# Which provider family each wrapper belongs to, so a message about someone
# else's CLI is not read as this worker's lane dying.
PROVIDER_CLI_FAMILY = {
    "codex": "codex",
    "claude": "claude",
    "antigravity": "antigravity",
    "gemini": "antigravity",
    "copilot": "copilot",
    "github": "copilot",
}


def provider_launcher_missing_cli(text: str | None) -> str | None:
    """Return the CLI name a wrapper reported as missing, if any."""

    match = PROVIDER_LAUNCHER_MISSING_PATTERN.search((text or "").strip())
    return match.group("cli").lower() if match else None


def command_exists(name: str | None) -> str | None:
    """Resolve an executable to an absolute path, or None when unavailable.

    Bare names are looked up on PATH. Path-like config values (for example
    ".orchestrator/bin/agy") are tried against the current directory first, then
    against the repo root, so resolution no longer depends on the caller's cwd.

    The result is always absolute: callers spawn the resolved command with `cwd`
    set to a workspace/worktree rather than the repo root, where a relative
    argv[0] would resolve against the wrong tree.
    """
    if not name:
        return None
    candidate = os.path.expanduser(str(name).strip())
    if not candidate:
        return None

    resolved = shutil.which(candidate)
    separators = {os.sep, os.altsep} - {None}
    if resolved is None and not os.path.isabs(candidate) and any(sep in candidate for sep in separators):
        resolved = shutil.which(str(ROOT / candidate))
    if resolved is None:
        return None
    return os.path.abspath(resolved)


def shell_quote(parts: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) if os.name == "nt" else __import__("shlex").quote(part) for part in parts)


def parse_iso_timestamp(ts: Any) -> datetime | None:
    """Parse an orchestrator ISO-8601 timestamp, tolerating junk.

    Every module used to carry its own copy of this four-line helper under a
    different private name, and they had drifted: the strict variants only caught
    ``ValueError``, so a non-string value raised ``AttributeError`` out of
    ``.replace`` instead of reading as "no timestamp".
    """
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def pid_is_alive(pid: Any) -> bool:
    """Return True when ``pid`` names a process that can still do work.

    This is the single liveness check for the whole orchestrator.  It used to be
    re-implemented per module with quietly different answers; the cheap variants
    (``os.path.exists("/proc/<pid>")`` or a bare ``kill(0)``) report a **zombie**
    as alive, because an exited-but-unreaped child keeps both its ``/proc`` entry
    and its signal target.  That made finished workers look like running ones --
    e.g. approval pruning skipped their orphaned approvals forever.
    """
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return False
    except OSError:
        # ChildProcessError for anything that is not our child; nothing to reap.
        pass
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            parts = proc_stat.read_text(encoding="utf-8", errors="ignore").split()
        except OSError:
            parts = []
        # Field 3 is the process state; "Z" is an exited, not-yet-reaped child.
        if len(parts) >= 3 and parts[2] == "Z":
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, just owned by another user.
        return True
    except OSError:
        return False
    return True


# `.orchestrator/bin/gh` is a broker shim, not the real CLI. Recognised by this
# suffix so a hit anywhere on PATH is caught, not just the one under ROOT.
GH_BROKER_SHIM_SUFFIX = ".orchestrator/bin/gh"
# Standard locations for the real GitHub CLI, tried when PATH resolves to the
# broker shim or to nothing at all.
SYSTEM_GH_PATHS = ("/usr/bin/gh", "/usr/local/bin/gh")


def resolve_github_cli(repo_root: Path | None = None) -> str | None:
    """Resolve the real `gh`, never preferring `.orchestrator/bin/gh`.

    That file is a broker shim, not the real CLI
    (delivery_toolchain/git/README.md). This rule was previously written out five
    times -- task_finalize.sh, check_pr_merge_eligibility.py,
    apply_branch_protection.py, ai_status.py and github_bus.resolve_gh_binary --
    and they had drifted: the first four rejected the shim outright while the
    fifth actively preferred it, which is how the GitHub bus ended up as the only
    consumer in the system routed through a shim that could not run.

    Order: whatever PATH gives, unless that is the shim; then the standard system
    paths; then the shim as a genuine last resort, because on a host with no real
    CLI running it yields its "GitHub CLI binary not found" diagnostic, which the
    orchestrator recognises, rather than a bare ENOENT.

    Returns None when nothing is installed. Callers that must hand a string to
    subprocess spell their own fallback.
    """
    found = shutil.which("gh")
    if found and not found.replace(os.sep, "/").endswith(GH_BROKER_SHIM_SUFFIX):
        return found
    for candidate in SYSTEM_GH_PATHS:
        if os.access(candidate, os.X_OK):
            return candidate
    vendored = (repo_root or ROOT) / GH_BROKER_SHIM_SUFFIX
    if vendored.exists() and os.access(vendored, os.X_OK):
        return str(vendored)
    return None


def supervisor_pid_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.pid"


def supervisor_lock_path(config: dict[str, Any]) -> Path:
    return config_path(config, "state_file").parent / "supervisor.lock"


def cmdline_is_supervisor_process(parts: list[str]) -> bool:
    """Return True when ``parts`` is a supervisor's own argv, not a wrapper's.

    ``timeout 20s python3 supervisor.py`` and ``bash -lc "... supervisor.py"``
    carry the script path in their argv without being the supervisor. Matching
    any argument lets the singleton guard SIGTERM the wrapper and lets a health
    probe read the wrapper as a live supervisor, so require the script to be
    argv[0] or an argument of a ``python*`` executable.
    """
    if not parts:
        return False
    script = str(SUPERVISOR_SCRIPT_PATH)
    if parts[0] in {script, SUPERVISOR_SCRIPT_REL}:
        return True
    if not Path(parts[0]).name.startswith("python"):
        return False
    return any(
        part in {script, SUPERVISOR_SCRIPT_REL} or part.endswith(f"/{SUPERVISOR_SCRIPT_NAME}")
        for part in parts[1:]
    )


def pid_is_supervisor_process(pid: Any, repo_root: Path) -> bool:
    """Return True when ``pid`` is really *this repo's* supervisor.

    ``pid_is_alive`` alone is not enough for anything that decides whether the
    supervisor needs restarting.  ``supervisor.pid`` survives a SIGKILL (the
    atexit unlink never runs), and the kernel recycles pids -- so a stale pid
    file pointing at an unrelated process reads as a healthy supervisor forever.
    Verify the process identity, not just its existence.
    """
    if not pid_is_alive(pid):
        return False
    proc_dir = Path("/proc") / str(int(pid))
    try:
        cmdline = proc_dir.joinpath("cmdline").read_bytes()
        cwd = proc_dir.joinpath("cwd").resolve()
    except OSError:
        return False
    parts = [part.decode("utf-8", errors="ignore") for part in cmdline.split(b"\x00") if part]
    if cwd != repo_root.resolve():
        return False
    return cmdline_is_supervisor_process(parts)


def normalize_agent_id(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def display_name_for(config: dict[str, Any], agent_id: str) -> str:
    agent = config.get("agents", {}).get(normalize_agent_id(agent_id), {})
    return agent.get("display_name") or agent.get("name") or agent_id


def agent_config_for(config: dict[str, Any], agent_id: str) -> dict[str, Any]:
    normalized = normalize_agent_id(agent_id)
    agent = config.get("agents", {}).get(normalized)
    if agent:
        merged = deepcopy(agent)
        merged.setdefault("id", normalized)
        merged.setdefault("display_name", agent_id)
        return merged
    return {"id": normalized, "display_name": agent_id, "provider": normalized, "adapter": "file_inbox"}


def render_template(path: Path, variables: dict[str, Any]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


ACTIVITY_LOG_ROTATE_BYTES_DEFAULT = 50 * 1024 * 1024  # 50 MiB
ACTIVITY_LOG_ARCHIVE_SUBDIR = Path(".orchestrator") / "logs" / "activity-log-archive"


def _activity_log_rotate_threshold(config: dict[str, Any]) -> int:
    raw = (config.get("paths") or {}).get("activity_log_rotate_bytes")
    try:
        threshold = int(raw)
    except (TypeError, ValueError):
        return ACTIVITY_LOG_ROTATE_BYTES_DEFAULT
    return threshold if threshold > 0 else ACTIVITY_LOG_ROTATE_BYTES_DEFAULT


def _rotate_activity_log_if_needed(config: dict[str, Any], log_path: Path) -> None:
    try:
        size = log_path.stat().st_size
    except FileNotFoundError:
        return
    if size <= _activity_log_rotate_threshold(config):
        return
    archive_dir = ROOT / ACTIVITY_LOG_ARCHIVE_SUBDIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_dir / f"{log_path.stem}-{stamp}.jsonl.gz"
    counter = 1
    while archive_path.exists():
        archive_path = archive_dir / f"{log_path.stem}-{stamp}-{counter}.jsonl.gz"
        counter += 1
    rotating_path = log_path.with_suffix(log_path.suffix + ".rotating")
    try:
        os.replace(log_path, rotating_path)
    except FileNotFoundError:
        return
    try:
        with rotating_path.open("rb") as src, gzip.open(archive_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    finally:
        try:
            rotating_path.unlink()
        except FileNotFoundError:
            pass


def write_activity_log(config: dict[str, Any], entry: dict[str, Any]) -> None:
    payload = {
        "ts": utc_now(),
        "agent": "Orchestrator",
        **entry,
    }
    log_path = config_path(config, "activity_log")
    _rotate_activity_log_if_needed(config, log_path)
    append_jsonl(log_path, payload)


def runtime_log_path(prefix: str, target: str) -> Path:
    slug = normalize_agent_id(target) or "unknown"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = uuid.uuid4().hex[:6]
    return ORCHESTRATOR_DIR / "logs" / f"{stamp}-{prefix}-{slug}-{suffix}.log"


def new_runtime_id(prefix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def worker_runtime_paths(config: dict[str, Any], run_id: str) -> dict[str, Path]:
    safe_run_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(run_id or "worker")).strip("-") or "worker"
    try:
        root = config_path(config, "state_file").parent / "worker-runtime"
    except KeyError:
        try:
            root = config_path(config, "status_file").parent / ".orchestrator" / "worker-runtime"
        except KeyError:
            root = ORCHESTRATOR_DIR / "worker-runtime"
    return {
        "heartbeat_path": root / "heartbeats" / f"{safe_run_id}.json",
        "status_path": root / "status" / f"{safe_run_id}.json",
    }


def spawn_background_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    log_path: Path,
    env: dict[str, str] | None = None,
    run_id: str | None = None,
    heartbeat_path: Path | None = None,
    status_path: Path | None = None,
    heartbeat_interval_seconds: int = 15,
    runner_enabled: bool = True,
) -> tuple[subprocess.Popen[str], Path]:
    ensure_parent(log_path)
    command_to_spawn = list(command)
    spawn_env = env
    if runner_enabled and run_id:
        if heartbeat_path is None:
            heartbeat_path = log_path.with_suffix(log_path.suffix + ".heartbeat.json")
        if status_path is None:
            status_path = log_path.with_suffix(log_path.suffix + ".status.json")
        ensure_parent(heartbeat_path)
        ensure_parent(status_path)
        spawn_env = dict(env or os.environ)
        spawn_env["ORCH_RUN_ID"] = str(run_id)
        spawn_env["ORCH_HEARTBEAT_PATH"] = str(heartbeat_path)
        spawn_env["ORCH_RUNNER_STATUS_PATH"] = str(status_path)
        command_to_spawn = [
            sys.executable,
            str(ORCHESTRATOR_DIR / "worker_runner.py"),
            "--run-id",
            str(run_id),
            "--heartbeat-path",
            str(heartbeat_path),
            "--status-path",
            str(status_path),
            "--heartbeat-interval-seconds",
            str(max(1, int(heartbeat_interval_seconds or 15))),
            "--",
            *command,
        ]
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command_to_spawn,
        cwd=str(cwd or ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=spawn_env,
        start_new_session=True,
    )
    return process, log_path


def snapshot_task(task: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": task.get(schema["task_id_field"]),
        "status": task.get(schema["status_field"]),
        "owner": task.get(schema["assignee_field"]),
        "reviewer": task.get(schema["reviewer_field"]),
        "artifacts": list(task.get(schema.get("artifacts_field", "artifacts"), []) or []),
        "next": task.get(schema.get("next_field", "next")),
        "last_update": task.get(schema.get("last_update_field", "last_update")),
    }
    for key in (
        "task_class",
        "auto_generated",
        "helper_parent",
        "helper_kind",
        "mutates_canonical",
        "auto_created_by",
        "source_plane",
        "source_ref",
        "materialization_ref",
    ):
        if key in task:
            payload[key] = task.get(key)
    return payload


def load_status(config: dict[str, Any]) -> dict[str, Any]:
    return load_json(config_path(config, "status_file"), default={}) or {}


def planning_shared_files(planning_state: dict[str, Any] | None = None) -> list[Path]:
    state = planning_state if planning_state is not None else (load_json(PLANNING_STATE_PATH, default={}) or {})
    if str(state.get("status") or "") not in {"active", "human_required"}:
        return []

    files: list[Path] = []
    readme_path = resolve_path(((state.get("artifacts", {}) or {}).get("planning_readme", {}) or {}).get("path"))
    session_path = resolve_path(state.get("session_file"))
    for candidate in (readme_path, session_path):
        if candidate and candidate.exists():
            files.append(candidate)

    if not files:
        for path in DEFAULT_PLANNING_SHARED_FILES:
            if path.exists():
                files.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def selected_shared_files(config: dict[str, Any]) -> list[Path]:
    files: list[Path] = []
    for key in ("status_file", "current_work", "activity_log", "dashboard"):
        path = config.get("paths", {}).get(key)
        if path:
            files.append(config_path(config, key))
    files.extend(planning_shared_files())
    return files


def compact_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def approval_tool_input_signature(tool_input: Any) -> str:
    try:
        payload = stable_json(tool_input if tool_input is not None else {})
    except TypeError:
        payload = compact_whitespace(tool_input)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approval_tool_input_preview(tool_input: Any, *, limit: int = 220) -> str:
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "raw_command", "query", "path", "file", "url"):
            value = compact_whitespace(tool_input.get(key))
            if value:
                return value[:limit]
        preview = compact_whitespace(stable_json(tool_input))
        return preview[:limit]
    if isinstance(tool_input, list):
        preview = compact_whitespace(stable_json(tool_input))
        return preview[:limit]
    return compact_whitespace(tool_input)[:limit]


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


def summarize_failure_reason(reason: str | None, provider: str | None = None, *, limit: int = 180) -> dict[str, str]:
    raw = compact_whitespace(reason)
    provider_label = str(provider or "").strip() or "provider"
    if not raw:
        return {"kind": "unknown", "summary": f"{provider_label} failure", "detail": ""}

    lowered = raw.lower()
    if is_github_cli_auth_failure(raw):
        return {"kind": "tool_auth", "summary": "GitHub CLI auth unavailable", "detail": raw[: max(420, limit)]}
    if "you have no quota" in lowered:
        return {"kind": "quota", "summary": "402 You have no quota", "detail": raw[: max(420, limit)]}
    if "credit balance is too low" in lowered or "billing_error" in lowered:
        return {"kind": "quota", "summary": "Credit balance is too low", "detail": raw[: max(420, limit)]}
    if "free daily quota has been reached" in lowered:
        return {"kind": "quota", "summary": "Daily quota exceeded", "detail": raw[: max(420, limit)]}
    if "hit your usage limit" in lowered:
        return {"kind": "quota", "summary": "Codex usage limit reached", "detail": raw[: max(420, limit)]}
    if "hit your limit" in lowered:
        return {"kind": "quota", "summary": "Rate limit reached", "detail": raw[: max(420, limit)]}
    if "config.toml" in lowered and (
        "error loading" in lowered
        or "cannot be parsed" in lowered
        or "unsupported service_tier" in lowered
        or "unknown variant" in lowered
    ):
        return {"kind": "provider_config", "summary": "Provider config invalid", "detail": raw[: max(420, limit)]}
    if "rate limit" in lowered or "rate limited" in lowered or "capacity" in lowered or "quota exceeded" in lowered or "quota_exhausted" in lowered or "free tier quota exceeded" in lowered:
        return {"kind": "capacity", "summary": "Capacity / rate limit failure", "detail": raw[: max(420, limit)]}
    if "unauthorized" in lowered or "authentication" in lowered or "invalid api key" in lowered:
        return {"kind": "auth", "summary": "Authentication failure", "detail": raw[: max(420, limit)]}
    if "an unexpected critical error occurred" in lowered:
        return {"kind": "unknown_critical", "summary": "Unexpected critical provider failure", "detail": raw[: max(420, limit)]}
    return {"kind": "terminal", "summary": raw[:limit], "detail": raw[: max(420, limit)]}


def task_brief_path(task_id: str | None) -> Path:
    slug = normalize_agent_id(task_id or "unknown-task") or "unknown-task"
    return TASK_BRIEFS_DIR / f"{slug}.md"


def _recent_task_activity(config: dict[str, Any], task_id: str, *, limit: int = 6) -> list[dict[str, Any]]:
    try:
        path = config_path(config, "activity_log")
    except Exception:
        return []
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    chunk_size = 64 * 1024
    max_scan_bytes = 16 * 1024 * 1024
    scanned = 0

    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""

        while position > 0 and len(entries) < limit and scanned < max_scan_bytes:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            scanned += read_size
            buffer = chunk + buffer
            lines = buffer.splitlines()

            if position > 0:
                buffer = lines[0] if lines else buffer
                complete_lines = lines[1:]
            else:
                buffer = b""
                complete_lines = lines

            for raw_line in reversed(complete_lines):
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # Ignore a partially-written tail line rather than stalling dispatch.
                    continue
                if str(entry.get("task_id") or "").strip() != task_id:
                    continue
                entries.append(entry)
                if len(entries) >= limit:
                    break

    entries.reverse()
    return entries


def normalize_source_doc_path(rel_path: str) -> str:
    path_str = str(rel_path or "").strip().replace("\\", "/")
    while path_str.startswith("./"):
        path_str = path_str[2:]
    return path_str.lstrip("/")


def validate_source_doc_path(
    rel_path: str,
    status_root: Path,
    *,
    task: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[bool, str, str | None]:
    raw_str = str(rel_path or "").strip().replace("\\", "/")
    if raw_str.startswith("/") or Path(raw_str).is_absolute():
        return False, raw_str, "raw absolute path rejected"
    if config is not None:
        try:
            from source_document_router import (
                SourceDocumentRoutingError,
                resolve_source_document,
            )
            resolved = resolve_source_document(
                config,
                status_root,
                raw_str,
                task=task,
            )
            return True, resolved.context_path, None
        except SourceDocumentRoutingError as exc:
            return False, raw_str, str(exc)
    norm = normalize_source_doc_path(rel_path)
    if not norm:
        return False, norm, "empty path"
    parts = Path(norm).parts
    if ".." in parts:
        return False, norm, "traversal path rejected"
    try:
        resolved_status_root = status_root.resolve()
        target = (status_root / norm).resolve()
        target.relative_to(resolved_status_root)
    except Exception:
        return False, norm, "external or traversal path rejected"

    if not target.exists():
        return False, norm, "missing source document"

    if target.is_dir():
        inventory_candidates = ["manifest.json", "inventory.json", ".inventory", "LATEST.json"]
        has_inventory = any((target / inv).exists() for inv in inventory_candidates)
        if not has_inventory:
            return False, norm, "directory without inventory manifest"

        for item in target.rglob("*"):
            try:
                resolved_item = item.resolve()
                resolved_item.relative_to(resolved_status_root)
            except Exception:
                return False, norm, f"external directory child symlink rejected for '{item.relative_to(status_root)}'"

    return True, norm, None


def validate_destination_context_path(
    rel_context_path: str,
    workspace_path: Path,
) -> tuple[bool, Path, str]:
    """Validate that a relative context file destination path stays safely beneath workspace_path.

    Returns (is_valid, destination_path, error_reason).
    """
    rel_str = str(rel_context_path or "").strip().replace("\\", "/")
    if not rel_str or Path(rel_str).is_absolute():
        return False, workspace_path, "empty or absolute destination path rejected"

    norm_rel = normalize_source_doc_path(rel_str)
    if not norm_rel:
        return False, workspace_path, "empty destination path rejected"

    parts = Path(norm_rel).parts
    if ".." in parts:
        return False, workspace_path, f"traversal destination path rejected for '{rel_str}'"

    resolved_workspace = workspace_path.resolve()
    destination = workspace_path / norm_rel

    # Check 1: Destination resolution
    try:
        resolved_dest = destination.resolve()
        resolved_dest.relative_to(resolved_workspace)
    except (ValueError, RuntimeError):
        return False, destination, f"destination path '{rel_str}' escapes workspace root"

    # Check 2: Check every path component from workspace_path to destination
    curr = workspace_path
    for part in parts:
        curr = curr / part
        if os.path.islink(curr) or curr.is_symlink():
            return (
                False,
                destination,
                f"destination component '{part}' is a symlink",
            )

    return True, destination, ""



def task_brief_canonical_hash(task: dict[str, Any]) -> str:
    task_id = str(task.get("id") or "").strip()
    source_docs = [normalize_source_doc_path(str(item)) for item in (task.get("source_docs") or []) if str(item).strip()]
    acceptance = [str(item).strip() for item in (task.get("acceptance") or []) if str(item).strip()]
    verification = [str(item).strip() for item in (task.get("verification") or []) if str(item).strip()]
    depends_on = [str(item).strip() for item in (task.get("depends_on") or []) if str(item).strip()]
    artifacts = [str(item).strip() for item in (task.get("artifacts") or []) if str(item).strip()]
    canonical_payload = {
        "id": task_id,
        "title": task.get("title"),
        "status": str(task.get("status") or "-"),
        "owner": str(task.get("owner") or "-"),
        "reviewer": str(task.get("reviewer") or "-"),
        "phase": str(task.get("phase") or "-"),
        "summary_zh": str(task.get("summary_zh") or "-"),
        "last_update": str(task.get("last_update") or "-"),
        "next": task.get("next"),
        "depends_on": depends_on,
        "artifacts": artifacts,
        "source_docs": source_docs,
        "acceptance": acceptance,
        "verification": verification,
    }
    return hashlib.sha256(json.dumps(canonical_payload, sort_keys=True).encode("utf-8")).hexdigest()


def is_task_brief_stale(text: str, task: dict[str, Any]) -> bool:
    if not text or not isinstance(task, dict):
        return True

    patterns = {
        "status": [r"^-\s*Status:\s*(.+)$"],
        "owner": [r"^-\s*Owner:\s*(.+)$"],
        "reviewer": [r"^-\s*Reviewer:\s*(.+)$"],
        "last_update": [r"^-\s*Last update:\s*(.+)$", r"^-\s*Last Update:\s*(.+)$", r"^-\s*Task Last Update:\s*(.+)$"],
    }

    for field, regexes in patterns.items():
        expected = str(task.get(field) or "").strip()
        if not expected or expected == "-":
            continue
        found_val = None
        for regex in regexes:
            match = re.search(regex, text, re.MULTILINE | re.IGNORECASE)
            if match:
                found_val = match.group(1).strip()
                break
        if found_val is not None and found_val.lower() != expected.lower():
            return True

    sha_match = re.search(r"^-\s*SHA256:\s*([a-fA-F0-9]{64})$", text, re.MULTILINE)
    if not sha_match:
        return True
    expected_hash = task_brief_canonical_hash(task)
    if sha_match.group(1).lower() != expected_hash.lower():
        return True

    expected_docs = [normalize_source_doc_path(str(item)) for item in (task.get("source_docs") or []) if str(item).strip()]
    source_docs_match = re.search(r"^##\s*Source Documents\s*\n((?:(?!\n##\s).)*)", text, re.MULTILINE | re.DOTALL)
    if not source_docs_match:
        return True

    block = source_docs_match.group(1)
    found_docs: list[str] = []
    for line in block.splitlines():
        line_str = line.strip()
        if line_str.startswith("-"):
            val = line_str[1:].strip()
            if val and val.lower() != "none":
                found_docs.append(normalize_source_doc_path(val))
    if found_docs != expected_docs:
        return True

    return False


def validate_task_archive_ambiguity(config: dict[str, Any], task_id: str | None) -> None:
    if not task_id:
        return
    status_data = load_status(config)
    tasks = status_data.get("tasks", []) or []
    active_task = next((t for t in tasks if str(t.get("id") or "").strip() == task_id), None)
    s_root = delivery_status_root(config)
    archive_file = s_root / "ai-task-archive" / "tasks" / f"{task_id}.json"
    archived_task = None
    if archive_file.exists():
        snapshot = load_json(archive_file, default=None)
        if isinstance(snapshot, dict) and isinstance(snapshot.get("task"), dict):
            archived_task = snapshot["task"]
    if not archived_task:
        from task_archive import load_archived_task
        archived_task = load_archived_task(task_id)

    if active_task and archived_task:
        for k in ("status", "owner", "reviewer", "last_update", "title", "phase", "summary_zh", "next"):
            active_val = str(active_task.get(k) or "").strip()
            archived_val = str(archived_task.get(k) or "").strip()
            if active_val != archived_val:
                raise ValueError(
                    f"Archived-task ambiguity for task {task_id}: active {k}='{active_task.get(k)}' != archived {k}='{archived_task.get(k)}'"
                )

        for k in ("depends_on", "artifacts", "source_docs", "acceptance", "verification"):
            if k == "source_docs":
                active_list = [normalize_source_doc_path(str(x)) for x in (active_task.get(k) or []) if str(x).strip()]
                archived_list = [normalize_source_doc_path(str(x)) for x in (archived_task.get(k) or []) if str(x).strip()]
            else:
                active_list = [str(x).strip() for x in (active_task.get(k) or []) if str(x).strip()]
                archived_list = [str(x).strip() for x in (archived_task.get(k) or []) if str(x).strip()]

            if active_list != archived_list:
                raise ValueError(
                    f"Archived-task ambiguity for task {task_id}: active {k}={active_list} != archived {k}={archived_list}"
                )


def generate_task_brief_content(
    config: dict[str, Any],
    task_id: str | None,
    *,
    generated_at: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    if not task_id:
        raise ValueError("task_id is required")
    status_data = load_status(config)
    tasks = status_data.get("tasks", []) or []
    from task_archive import TaskResolver

    resolver = TaskResolver(tasks)

    active_task = next((t for t in tasks if str(t.get("id") or "").strip() == task_id), None)
    s_root = delivery_status_root(config)
    archive_file = s_root / "ai-task-archive" / "tasks" / f"{task_id}.json"
    archived_task = None
    if archive_file.exists():
        snapshot = load_json(archive_file, default=None)
        if isinstance(snapshot, dict) and isinstance(snapshot.get("task"), dict):
            archived_task = snapshot["task"]
    if not archived_task:
        from task_archive import load_archived_task
        archived_task = load_archived_task(task_id)

    validate_task_archive_ambiguity(config, task_id)

    task = active_task or archived_task
    if task is None:
        raise ValueError(f"Task not found: {task_id}")

    deps = [resolver.get(dep_id) for dep_id in (task.get("depends_on") or [])]
    deps = [item for item in deps if item]
    planning_state = load_json(PLANNING_STATE_PATH, default={}) or {}
    planning_active = str(planning_state.get("status") or "") in {"active", "human_required", "accepted"}
    source_ref = task.get("source_ref") if isinstance(task.get("source_ref"), dict) else {}
    source_plane = str(task.get("source_plane") or "").strip()
    source_docs = [normalize_source_doc_path(str(item)) for item in (task.get("source_docs") or []) if str(item).strip()]
    acceptance = [str(item).strip() for item in (task.get("acceptance") or []) if str(item).strip()]
    verification = [str(item).strip() for item in (task.get("verification") or []) if str(item).strip()]
    recent = _recent_task_activity(config, task_id)
    artifacts = [str(item).strip() for item in (task.get("artifacts") or []) if str(item).strip()]

    rel_source_path = relpath(task_brief_path(task_id))
    gen_time = generated_at or utc_now()
    task_last_update = str(task.get("last_update") or "-")
    task_status_val = str(task.get("status") or "-")
    task_owner_val = str(task.get("owner") or "-")
    task_reviewer_val = str(task.get("reviewer") or "-")

    sha256_hash = task_brief_canonical_hash(task)

    header_lines = [
        f"# Task Brief: {task_id}",
        "",
        f"- Source Path: {rel_source_path}",
        f"- Generated At: {gen_time}",
        f"- Task Last Update: {task_last_update}",
        f"- Status: {task_status_val}",
        f"- Owner: {task_owner_val}",
        f"- Reviewer: {task_reviewer_val}",
        f"- SHA256: {sha256_hash}",
        "",
        "This file is generated by the orchestrator for task-scoped execution context.",
        "Treat `ai-status.json` as the durable execution source of truth only when you need to verify or update state.",
        "Do not read `current-work.md` by default for implementation context.",
        "",
        "## Task",
        f"- Title: {task.get('title') or '-'}",
        f"- Status: {task_status_val}",
        f"- Owner: {task_owner_val}",
        f"- Reviewer: {task_reviewer_val}",
        f"- Phase: {task.get('phase') or '-'}",
        f"- Last update: {task_last_update}",
        f"- Next: {compact_whitespace(task.get('next') or '-')}",
        "",
        "## Summary",
        f"{task.get('summary_zh') or '-'}",
        "",
        "## Dependencies",
    ]

    body = list(header_lines)
    if deps:
        body.extend(
            f"- {dep.get('id')}: {resolver.dependency_status(dep.get('id'))} · {compact_whitespace(dep.get('title') or dep.get('summary_zh') or '-')}"
            for dep in deps
        )
    else:
        body.append("- none")

    body.extend(["", "## Artifacts"])
    body.extend([f"- {item}" for item in artifacts] or ["- none"])
    body.extend(["", "## Source Documents"])
    body.extend([f"- {item}" for item in source_docs] or ["- none"])
    body.extend(["", "## Acceptance"])
    body.extend([f"- {item}" for item in acceptance] or ["- none"])
    body.extend(["", "## Verification"])
    body.extend([f"- `{item}`" for item in verification] or ["- none"])
    body.extend(["", "## Recent Task Activity"])
    if recent:
        body.extend(
            f"- {entry.get('ts') or '-'} · {entry.get('agent') or '-'} · {entry.get('type') or '-'} · {compact_whitespace(entry.get('message') or '-')}"
            for entry in recent
        )
    else:
        body.append("- none")

    body.extend(["", "## Relevant Canonical Files", "- AI_COLLABORATION_GUIDE.md", "- ai-status.json"])
    if planning_active:
        session_file = str(planning_state.get("session_file") or "").strip()
        if session_file:
            body.append(f"- {session_file}")
        else:
            fallback_planning_files = planning_shared_files(planning_state)
            if fallback_planning_files:
                body.append(f"- {relpath(fallback_planning_files[0])}")

    if source_plane or source_ref:
        body.extend(["", "## Planning Origin"])
        body.append(f"- Source plane: {source_plane or '-'}")
        if source_ref:
            for label, key in (
                ("Session", "session_id"),
                ("Phase", "phase"),
                ("Profile", "profile"),
                ("Planning dir", "planning_dir"),
                ("Session file", "session_file"),
                ("Consensus packet", "consensus_packet"),
                ("Execution materialization", "execution_materialization"),
            ):
                value = str(source_ref.get(key) or "").strip()
                if value:
                    body.append(f"- {label}: {value}")

    body.extend([f"- {item}" for item in artifacts[:6] if item not in {"AI_COLLABORATION_GUIDE.md", "ai-status.json"}])
    body.extend(
        [
            "",
            "## Working Rules",
            "- Use scripts/ai-status.sh or python3 scripts/ai_status.py for status changes.",
            "- Keep execution updates short and structured.",
            "- If you need raw provider/debug details, ask for the relevant runtime log or evidence ref instead of scanning global summaries.",
            "",
        ]
    )

    full_text = "\n".join(body)
    return full_text, sha256_hash, task


def write_task_brief(config: dict[str, Any], task_id: str | None) -> Path | None:
    if not task_id:
        return None
    validate_task_archive_ambiguity(config, task_id)
    path = task_brief_path(task_id)
    ensure_parent(path)

    status_data = load_status(config)
    tasks = status_data.get("tasks", []) or []
    from task_archive import TaskResolver

    resolver = TaskResolver(tasks)
    task = resolver.get(task_id)
    if task is None:
        return None

    if path.exists():
        existing_text = path.read_text(encoding="utf-8")
        if not is_task_brief_stale(existing_text, task):
            return path

    text, _, _ = generate_task_brief_content(config, task_id)
    path.write_text(text, encoding="utf-8")
    return path


def execution_context_files(config: dict[str, Any], task_id: str | None) -> list[str]:
    files = ["AI_COLLABORATION_GUIDE.md"]
    try:
        status_root = config_path(config, "status_file", default=str(ROOT / "ai-status.json")).parents[0].resolve()
    except KeyError:
        status_root = ROOT.resolve()

    status_data = load_status(config)
    tasks = status_data.get("tasks", []) or []
    from task_archive import TaskResolver

    resolver = TaskResolver(tasks)
    task = resolver.get(task_id)

    is_mutating_or_p0 = False
    if task:
        is_mutating_or_p0 = (
            str(task.get("priority") or "").upper() == "P0"
            or bool(task.get("mutates_canonical"))
            or str(task.get("phase") or "").strip() != "Unassigned"
        )
        source_docs = task.get("source_docs") or []
        for doc_entry in source_docs:
            valid, norm_path, err_reason = validate_source_doc_path(
                doc_entry, status_root, task=task, config=config
            )
            if not valid:
                if is_mutating_or_p0:
                    raise ValueError(f"Fail-closed on task {task_id}: {err_reason} for source_doc '{doc_entry}'")
            else:
                files.append(norm_path)

    try:
        brief = write_task_brief(config, task_id)
    except Exception as exc:
        if is_mutating_or_p0:
            raise
        write_activity_log(
            config,
            {
                "type": "task_brief_generation_failed",
                "task_id": task_id,
                "message": f"Fell back to minimal execution context after task brief generation failed: {type(exc).__name__}: {exc}",
            },
        )
        files.append("ai-status.json")
        return unique_strings(files)

    if brief is not None:
        files.append(relpath(brief))
    if WORKER_ANCHOR_SPEC_PATH.exists():
        files.append(relpath(WORKER_ANCHOR_SPEC_PATH))
    if CLOSEOUT_SPEC_PATH.exists():
        files.append(relpath(CLOSEOUT_SPEC_PATH))
    files.append("ai-status.json")
    return unique_strings(files)


def write_failure_evidence(
    config: dict[str, Any],
    *,
    worker: dict[str, Any],
    reason: str | None,
    failure_kind: str | None = None,
) -> str | None:
    run_id = str(worker.get("run_id") or "").strip()
    if not run_id:
        return None
    path = evidence_dir(config) / f"{normalize_agent_id(run_id) or run_id}.json"
    ensure_parent(path)
    payload = {
        "recorded_at": utc_now(),
        "task_id": worker.get("task_id"),
        "run_id": run_id,
        "provider": worker.get("provider"),
        "agent_id": worker.get("agent_id"),
        "failure_kind": failure_kind,
        "reason": reason or "",
        "log_path": worker.get("log_path"),
        "session_id": worker.get("session_id"),
        "queue_event_id": worker.get("queue_event_id"),
    }
    write_json(path, payload)
    return relpath(path)


def write_approval_evidence(
    config: dict[str, Any],
    *,
    approval_id: str | None,
    stage: str,
    payload: dict[str, Any],
) -> str | None:
    approval_slug = normalize_agent_id(approval_id or "approval") or "approval"
    stage_slug = normalize_agent_id(stage) or "event"
    path = evidence_dir(config) / f"{approval_slug}-{stage_slug}.json"
    ensure_parent(path)
    write_json(
        path,
        {
            "recorded_at": utc_now(),
            "approval_id": approval_id,
            "stage": stage,
            **payload,
        },
    )
    return relpath(path)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


if __name__ == "__main__":
    print("This module is shared by the orchestrator scripts and is not meant to be run directly.", file=sys.stderr)
    raise SystemExit(1)
