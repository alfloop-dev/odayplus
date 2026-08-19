#!/usr/bin/env python3
"""Sign the Antigravity CLI (`agy`) in via Google OAuth on a headless host.

`agy` has no `login` subcommand: sign-in only happens through the interactive
onboarding TUI, which prints a Google OAuth URL and then waits for the operator
to paste back the authorization code shown in the browser. The PKCE verifier
lives in that process, so the code must be pasted into the *same* run -- this
helper therefore keeps the CLI alive, unwraps the URL from its box, and feeds the
code back in.

The OAuth token is written under the provider's HOME override (so antigravity2
gets its own credential) at the exact path AntigravityAdapter checks:

    python3 .orchestrator/antigravity_login_helper.py --provider antigravity
    python3 .orchestrator/antigravity_login_helper.py --provider antigravity2
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from adapters.antigravity import _antigravity_home, _auth_ready, _oauth_token_path
from common import command_exists, load_config
from provider_runtime import provider_env, provider_section
from pty_login import PtyLoginSession, unwrap_boxed_text

ROOT = THIS_DIR.parent

MENU_MARKER = "Select login method:"
URL_MARKER = "Open the URL below in your browser"
CODE_PROMPT_MARKER = "copy the code displayed in the browser"
OAUTH_URL_PATTERN = re.compile(r"https://accounts\.google\.com/o/oauth2/auth\?\S+")

MENU_TIMEOUT = 120.0
URL_TIMEOUT = 120.0
DEFAULT_TOKEN_TIMEOUT = 180.0
DEFAULT_CODE_WAIT = 900.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drive the Antigravity CLI Google OAuth sign-in on a pty."
    )
    parser.add_argument(
        "--provider",
        default="antigravity",
        help="Provider key in .orchestrator/config.json (e.g. antigravity, antigravity2).",
    )
    parser.add_argument(
        "--auth-code",
        help="Authorization code from the browser. Omit to be prompted (requires a TTY).",
    )
    parser.add_argument(
        "--auth-code-file",
        help=(
            "Path the authorization code will be written to. The helper prints the OAuth URL, "
            "then waits for this file to appear. Use when there is no TTY to prompt on "
            "(supervisor or agent-driven runs), since the code must be redeemed by this same process."
        ),
    )
    parser.add_argument(
        "--code-wait",
        type=float,
        default=DEFAULT_CODE_WAIT,
        help=f"Seconds to wait for --auth-code-file. Default {DEFAULT_CODE_WAIT:.0f}.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TOKEN_TIMEOUT,
        help=f"Seconds to wait for the token file after pasting the code. Default {DEFAULT_TOKEN_TIMEOUT:.0f}.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run sign-in even if a token already exists for this provider.",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only report whether the provider already has an OAuth token.",
    )
    return parser.parse_args(argv)


def resolve_cli(config: dict, provider_id: str) -> str:
    """Mirror AntigravityAdapter: configured CLI, repo-relative shim, then `agy`."""
    settings = provider_section(
        config, provider_id=provider_id, section="antigravity", default="antigravity"
    )
    configured = str(settings.get("cli") or "agy").strip()
    resolved = command_exists(configured) or command_exists("agy")
    if resolved:
        return resolved
    raise SystemExit(f"Antigravity CLI not found (configured as {configured!r})")


def build_env(config: dict, provider_id: str) -> tuple[dict[str, str], Path]:
    env = dict(os.environ)
    env.update(
        provider_env(
            config,
            default="antigravity",
            provider_id=provider_id,
            blocks=("runtime", "antigravity"),
        )
    )
    home = _antigravity_home(config, provider_id)
    if home != Path.home():
        # Keep the bin/agy shim pointed at the real home so gh credentials survive.
        env.setdefault("PANTHEON_HOST_HOME", str(Path.home()))
        env["HOME"] = str(home)
        home.mkdir(parents=True, exist_ok=True)
    return env, home


def extract_oauth_url(buffer: str) -> str | None:
    """Pull the OAuth URL out of the TUI box, undoing its hard wrapping.

    The box is redrawn on every spinner tick, so scan from the last complete
    frame and only accept a URL that carries the trailing `state=` parameter.
    """
    start = buffer.rfind(URL_MARKER)
    if start < 0:
        return None
    region = buffer[start + len(URL_MARKER) :]
    end = region.find("After authenticating")
    if end >= 0:
        region = region[:end]
    joined = unwrap_boxed_text(region)
    match = OAUTH_URL_PATTERN.search(joined)
    if not match:
        return None
    url = match.group(0)
    if "state=" not in url:
        return None
    return url


def prompt_for_code(session: PtyLoginSession, url: str) -> str:
    """Ask the operator for the browser code while still pumping the CLI's pty."""
    print(
        "\n"
        "================ ANTIGRAVITY GOOGLE OAUTH ================\n"
        "  1. Open this URL in a browser (single line, no wrapping):\n\n"
        f"{url}\n\n"
        "  2. Approve access, then copy the authorization code shown.\n"
        "==========================================================",
        flush=True,
    )
    # Silence the TUI redraws so the prompt stays readable, but keep draining the
    # pty in the background: a full kernel pipe buffer would stall the CLI.
    session.echo_to_stdout = False
    stop = threading.Event()

    def pump() -> None:
        while not stop.is_set():
            session.drain(0.2)

    pump_thread = threading.Thread(target=pump, daemon=True)
    pump_thread.start()
    try:
        return input("Paste the authorization code here: ").strip()
    finally:
        stop.set()
        pump_thread.join(timeout=2)


def wait_for_code_file(path: Path, timeout: float, session: PtyLoginSession) -> str:
    """Block until the operator drops the browser code at ``path``.

    Keeps draining the pty while waiting so the CLI never stalls on a full
    output buffer during the (potentially long) browser round trip.
    """
    session.echo_to_stdout = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            code = path.read_text(encoding="utf-8", errors="replace").strip()
            if code:
                return code
        session.drain(0.5)
    return ""


def wait_for_token(token_path: Path, timeout: float, session: PtyLoginSession) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if token_path.exists() and token_path.stat().st_size > 0:
            return True
        session.drain(0.5)
    return token_path.exists() and token_path.stat().st_size > 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    provider_id = args.provider
    cli = resolve_cli(config, provider_id)
    env, home = build_env(config, provider_id)
    token_path = _oauth_token_path(config, provider_id)

    print(f"provider={provider_id} cli={cli} HOME={home}", flush=True)
    print(f"token_path={token_path}", flush=True)

    # Use the adapter's own readiness check so this agrees with dispatch-time
    # capability (it also accepts a GEMINI_API_KEY instead of an OAuth token).
    already = _auth_ready(config, provider_id)
    if args.status_only:
        print("signed_in" if already else "not_signed_in")
        return 0 if already else 1
    if already and not args.force:
        print("Already signed in (token present). Re-run with --force to replace it.")
        return 0

    with PtyLoginSession([cli], env=env, cwd=str(ROOT)) as session:
        if not session.wait_for(lambda buf: MENU_MARKER in buf, timeout=MENU_TIMEOUT):
            session.drain(2)
            print("\nAntigravity CLI never showed the login menu.", file=sys.stderr)
            return 2

        # "1. Google OAuth" is preselected; Enter accepts it.
        session.send("\r")

        if not session.wait_for(
            lambda buf: extract_oauth_url(buf) is not None and CODE_PROMPT_MARKER in buf,
            timeout=URL_TIMEOUT,
        ):
            session.drain(2)
            print("\nCould not read the Google OAuth URL from the CLI output.", file=sys.stderr)
            return 3

        url = extract_oauth_url(session.buffer)
        if not url:
            print("\nOAuth URL disappeared from the CLI output.", file=sys.stderr)
            return 3

        if args.auth_code:
            auth_code = args.auth_code.strip()
            print(f"\nOAuth URL:\n{url}\n\nUsing --auth-code from the command line.", flush=True)
            session.echo_to_stdout = False
        elif args.auth_code_file:
            code_file = Path(os.path.expanduser(args.auth_code_file))
            print(
                "\n"
                "================ ANTIGRAVITY GOOGLE OAUTH ================\n"
                "  1. Open this URL in a browser (single line, no wrapping):\n\n"
                f"{url}\n\n"
                "  2. Approve access, then copy the authorization code shown.\n"
                f"  3. Write that code to: {code_file}\n"
                "==========================================================",
                flush=True,
            )
            auth_code = wait_for_code_file(code_file, args.code_wait, session)
        elif sys.stdin.isatty():
            auth_code = prompt_for_code(session, url)
        else:
            print(f"\nOAuth URL:\n{url}", flush=True)
            print(
                "\nNo TTY to prompt on. Re-run with --auth-code-file (or --auth-code).\n"
                "The code must be redeemed by the same process that produced the URL, "
                "so this run cannot be resumed later.",
                file=sys.stderr,
            )
            return 4

        if not auth_code:
            print("No authorization code supplied.", file=sys.stderr)
            return 4

        session.send(auth_code + "\r")
        ok = wait_for_token(token_path, args.timeout, session)

    if not ok:
        print(f"\nNo OAuth token appeared at {token_path}.", file=sys.stderr)
        return 5
    print(f"\nAntigravity sign-in complete: {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
