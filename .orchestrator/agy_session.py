#!/usr/bin/env python3
"""Keep agy's supported stream input open until command tools are terminal.

Single-prompt print mode tears down background tasks when the agent returns.
Stream input instead waits for the conversation to become fully idle. Keep its
stdin open through that wait; EOF is sent only after a verified result. Receipts
contain lifecycle metadata, never prompts, command text, or command output.
"""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from worker_runner import write_json

TERMINAL = {"DONE", "ERROR", "CANCELLED", "CANCELED"}
INTERRUPTED = re.compile(r"^terminating [1-9]\d* background task\(s\) on exit$", re.I)
COMMAND_EXIT = re.compile(r"^\s*The command exited with code (-?\d+)\.")
CONVERSATION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def stream_command(argv: list[str]) -> tuple[list[str], str]:
    command: list[str] = []
    prompt: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"--prompt", "--print", "-p"}:
            if prompt is not None or i + 1 == len(argv):
                raise ValueError("expected exactly one agy prompt")
            prompt = argv[i + 1]
            i += 2
        elif any(arg.startswith(flag + "=") for flag in ("--prompt", "--print", "-p")):
            if prompt is not None:
                raise ValueError("expected exactly one agy prompt")
            prompt = arg.split("=", 1)[1]
            i += 1
        elif arg.split("=", 1)[0] in {"--input-format", "--output-format"}:
            raise ValueError("agy session owns its stream formats")
        else:
            command.append(arg)
            i += 1
    if not command or prompt is None:
        raise ValueError("agy session requires a command and --prompt")
    return command + ["--print=", "--input-format", "stream-json", "--output-format", "stream-json"], prompt


def command_exit_receipt(conversation: str, index: int) -> int | None:
    # Only read this session's native terminal header, not arbitrary paths or
    # tool stdout (which could quote the same text).
    if not CONVERSATION_ID.fullmatch(conversation):
        return None
    agy_home = Path(os.environ.get("ANTIGRAVITY_HOME") or Path.home())
    path = agy_home / ".gemini/antigravity-cli/brain" / conversation / ".system_generated/steps" / str(index) / "output.txt"
    try:
        with path.open(encoding="utf-8") as handle:
            header = handle.read(256)
    except OSError:
        return None
    match = COMMAND_EXIT.match(header)
    return int(match[1]) if match else None


def run_session(argv: list[str]) -> int:
    command, prompt = stream_command(argv)
    started = time.monotonic()
    path_value = os.environ.get("ORCH_RUNNER_STATUS_PATH")
    receipt_path = Path(path_value + ".agy.json") if path_value else None
    receipt: dict[str, Any] = {
        "schema_version": 1, "transport": "agy_stream_json", "status": "running",
        "conversation_id": None, "result_status": None, "cli_exit_code": None,
        "signal": None, "duration_seconds": None, "tools": [], "reason": None,
    }
    steps: dict[int, dict[str, Any]] = {}
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True)
    interrupted = False
    result_seen = False
    closed_at: float | None = None
    followups = 0
    previous_handlers: dict[int, Any] = {}

    def publish() -> None:
        receipt["duration_seconds"] = round(time.monotonic() - started, 3)
        receipt["tools"] = list(steps.values())
        if receipt_path:
            write_json(receipt_path, receipt)

    def stop(signum: int) -> None:
        # The CLI can exit before descendants release their inherited pipes.
        # This group belongs exclusively to this session, including those
        # descendants after the leader exits.
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    def signal_received(signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        receipt["signal"] = signum
        receipt["reason"] = "signal"
        stop(signum)

    def read(stream: Any, tag: str) -> None:
        try:
            for line in stream:
                events.put((tag, line))
        finally:
            events.put((tag, None))

    def send(content: str) -> None:
        assert child.stdin is not None
        child.stdin.write(json.dumps({"event": "user", "message": {"role": "user", "content": content}}) + "\n")
        child.stdin.flush()

    def close_input() -> None:
        nonlocal closed_at
        if closed_at is None:
            closed_at = time.monotonic()
            assert child.stdin is not None
            child.stdin.close()

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, signal_received)
        threading.Thread(target=read, args=(child.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=read, args=(child.stderr, "stderr"), daemon=True).start()
        publish()
        send(prompt)
        ended: set[str] = set()
        while len(ended) < 2 or child.poll() is None:
            if interrupted and closed_at is None:
                close_input()
            if closed_at is not None and time.monotonic() - closed_at > 10:
                interrupted = True
                receipt["reason"] = receipt["reason"] or "cli_exit_timeout"
                stop(signal.SIGKILL)
            try:
                tag, line = events.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                ended.add(tag)
                if tag == "stdout" and closed_at is None:
                    close_input()
                continue
            output = sys.stdout if tag == "stdout" else sys.stderr
            output.write(line)
            output.flush()
            if tag == "stderr":
                if INTERRUPTED.fullmatch(line.strip()):
                    interrupted = True
                    receipt["reason"] = "background_tasks_terminated"
                continue
            try:
                event = json.loads(line)
            except ValueError:
                interrupted = True
                receipt["reason"] = "invalid_stream_record"
                close_input()
                continue
            if not isinstance(event, dict):
                interrupted = True
                receipt["reason"] = "invalid_stream_record"
                close_input()
                continue
            if event.get("event") == "init":
                receipt["conversation_id"] = event.get("conversation_id")
            elif event.get("event") == "step_update":
                step = event.get("step_update")
                if not isinstance(step, dict):
                    interrupted = True
                    receipt["reason"] = "invalid_step_record"
                    close_input()
                    continue
                index = step.get("step_index")
                if step.get("step_type") == "tool" and isinstance(index, int) and index >= 0:
                    steps[index] = {
                        "step_index": index, "tool_name": step.get("tool_name"),
                        "state": step.get("state"), "duration_seconds": step.get("duration_seconds"),
                    }
                    if step.get("state") in {"CANCELLED", "CANCELED"}:
                        interrupted = True
                        receipt["reason"] = "tool_cancelled"
                publish()
            elif event.get("event") == "result":
                result_seen = True
                result = event.get("result")
                if not isinstance(result, dict):
                    interrupted = True
                    receipt["reason"] = "invalid_result_record"
                    close_input()
                    continue
                receipt["result_status"] = result.get("status")
                pending = [s["step_index"] for s in steps.values() if s["state"] not in TERMINAL]
                if pending and followups < 2 and not interrupted and result.get("status") == "SUCCESS":
                    # Defensive continuation if a CLI version yields a result
                    # before it is fully idle. Never rerun the commands.
                    followups += 1
                    send("Await the existing background tasks using manage_task status until terminal, "
                         "read their actual exit results, and finish the original task. Do not rerun "
                         f"these commands. Pending tool step indexes: {pending}.")
                else:
                    if pending:
                        interrupted = True
                        receipt["reason"] = "pending_tools_at_result"
                    close_input()
        cli_exit = child.wait()
        receipt["cli_exit_code"] = cli_exit
        for index, step in steps.items():
            if step["tool_name"] == "run_command":
                step["exit_code"] = command_exit_receipt(str(receipt["conversation_id"] or ""), index)
                if step["exit_code"] is None:
                    interrupted = True
                    receipt["reason"] = receipt["reason"] or "command_exit_unknown"
        pending = any(step["state"] not in TERMINAL for step in steps.values())
        successful = result_seen and receipt["result_status"] == "SUCCESS" and not interrupted and not pending and cli_exit == 0
        receipt["status"] = "completed" if successful else "interrupted" if interrupted or pending else "failed"
        receipt["reason"] = receipt["reason"] or (None if successful else "missing_or_failed_result")
        publish()
        if receipt["signal"]:
            return 128 + int(receipt["signal"])
        return 0 if successful else (cli_exit if cli_exit > 0 else 75)
    except BaseException as exc:
        receipt["status"] = "interrupted"
        receipt["reason"] = f"transport_exception:{type(exc).__name__}"
        publish()
        raise
    finally:
        stop(signal.SIGTERM)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stop(signal.SIGKILL)
            child.wait(timeout=5)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    try:
        return run_session(argv)
    except (ValueError, OSError) as exc:
        print(f"agy session failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
