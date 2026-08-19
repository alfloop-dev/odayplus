#!/usr/bin/env python3
"""Sign the Codex CLI in via device-code auth on a headless host.

`codex login` defaults to spawning a browser and listening on a loopback
callback port, which cannot work on the orchestrator hosts. `--device-auth`
prints a URL plus a one-time code instead, and this helper drives that flow on a
pty, surfaces the code, and verifies the resulting session.

Credentials are written to CODEX_HOME (resolved from the provider block in
.orchestrator/config.json) so the Codex adapter finds them at dispatch time.

    python3 .orchestrator/codex_login_helper.py --provider codex
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import command_exists, load_config
from pty_login import PtyLoginSession

ROOT = THIS_DIR.parent

DEVICE_URL_PATTERN = re.compile(r"https://\S*auth\.openai\.com/\S*device\S*")
DEVICE_CODE_PATTERN = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4,6})\b")
# Codex tells the user the code is good for 15 minutes; allow the full window.
DEFAULT_LOGIN_TIMEOUT = 900.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run `codex login --device-auth` on a pty and report the device code."
    )
    parser.add_argument(
        "--provider",
        default="codex",
        help="Provider key in .orchestrator/config.json used to resolve the CLI and CODEX_HOME.",
    )
    parser.add_argument(
        "--codex-home",
        help="Override CODEX_HOME instead of taking it from the provider config.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_LOGIN_TIMEOUT,
        help=f"Seconds to wait for the browser step. Defaults to {DEFAULT_LOGIN_TIMEOUT:.0f}.",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only report `codex login status` for the resolved provider; do not sign in.",
    )
    return parser.parse_args(argv)


def _codex_settings(config: dict, provider_id: str) -> dict:
    providers = config.get("providers", {}) or {}
    provider = providers.get(provider_id) or providers.get("codex") or {}
    return provider.get("codex", {}) or {}


def resolve_cli(settings: dict) -> str:
    """Mirror CodexAdapter.capability(): configured CLI first, then plain `codex`."""
    configured = str(settings.get("cli") or "codex").strip()
    resolved = command_exists(configured) or command_exists("codex")
    if resolved:
        return resolved
    raise SystemExit(f"codex CLI not found (configured as {configured!r})")


def build_env(settings: dict, codex_home_override: str | None) -> tuple[dict[str, str], str | None]:
    env = dict(os.environ)
    raw_home = codex_home_override or str(settings.get("codex_home") or "").strip()
    codex_home = os.path.expanduser(raw_home) if raw_home else None
    if codex_home:
        env["CODEX_HOME"] = codex_home
        Path(codex_home).mkdir(parents=True, exist_ok=True)
    # The adapter clears OPENAI_API_KEY unless a provider api_key_env is set;
    # an inherited key here would make Codex skip the ChatGPT sign-in entirely.
    if not str(settings.get("api_key_env") or "").strip():
        env.pop("OPENAI_API_KEY", None)
    return env, codex_home


def login_status(cli: str, env: dict[str, str]) -> tuple[int, str]:
    result = subprocess.run(
        [cli, "login", "status"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode, output


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    settings = _codex_settings(config, args.provider)
    cli = resolve_cli(settings)
    env, codex_home = build_env(settings, args.codex_home)

    print(f"provider={args.provider} cli={cli} CODEX_HOME={codex_home or '~/.codex (default)'}", flush=True)

    code, status_text = login_status(cli, env)
    if args.status_only:
        print(status_text or "(no output)")
        return 0 if code == 0 else 1
    if code == 0 and "not logged in" not in status_text.lower():
        print(f"Already signed in: {status_text}")
        return 0

    with PtyLoginSession([cli, "login", "--device-auth"], env=env, cwd=str(ROOT)) as session:
        found = session.wait_for(
            lambda buf: bool(DEVICE_URL_PATTERN.search(buf) and DEVICE_CODE_PATTERN.search(buf)),
            timeout=90,
        )
        if not found:
            session.drain(2)
            print("\nCould not read a device code from the Codex CLI output.", file=sys.stderr)
            return 2

        url_match = DEVICE_URL_PATTERN.search(session.buffer)
        code_match = DEVICE_CODE_PATTERN.search(session.buffer)
        url = url_match.group(0) if url_match else "https://auth.openai.com/codex/device"
        device_code = code_match.group(1) if code_match else ""

        print(
            "\n"
            "==================== CODEX DEVICE LOGIN ====================\n"
            f"  1. Open: {url}\n"
            f"  2. Enter code: {device_code}\n"
            f"DEVICE_CODE={device_code}\n"
            f"DEVICE_URL={url}\n"
            "Waiting for you to finish in the browser...\n"
            "============================================================",
            flush=True,
        )

        exit_code = session.wait_for_exit(timeout=args.timeout)
        if exit_code is None:
            print("\nTimed out waiting for the browser step to complete.", file=sys.stderr)
            return 3

    code, status_text = login_status(cli, env)
    print(f"\ncodex login status: {status_text or '(no output)'}")
    if code != 0 or "not logged in" in status_text.lower():
        print("Codex sign-in did not complete.", file=sys.stderr)
        return 4
    print("Codex sign-in complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
