"""Fixed offline manual parser child, launched only with Python -I -S.

No package installation, configurable import, credential, URL or storage read.
The parent owns original wall time, leases, quota and process-group reaping.
"""
import base64
import hashlib
import json
import math
import os
import resource
import stat
import sys
import types
from pathlib import Path
from uuid import UUID

MAX_INPUT = 750_000
MAX_OUTPUT = 750_000
MAX_PROVIDER = 262_144
MEMORY_BYTES = 128 * 1024 * 1024
CPU_SECONDS = 2
PROVIDER = Path(__file__).with_name("native_manual_json_parser.py")


def closed(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def invalid(_value):
    raise ValueError


def parse(request):
    if (not isinstance(request, dict) or set(request) != {"operation", "timeout_seconds", "nonce",
            "provider_sha256", "document_base64", "lineage"}
            or request["operation"] != "parse_manual"
            or type(request["timeout_seconds"]) not in {int, float}
            or not math.isfinite(request["timeout_seconds"]) or not 0 < request["timeout_seconds"] <= 10
            or not isinstance(request["nonce"], str) or len(request["nonce"]) != 36
            or not UUID(request["nonce"]).int or str(UUID(request["nonce"])) != request["nonce"]
            or not isinstance(request["provider_sha256"], str) or len(request["provider_sha256"]) != 64
            or not isinstance(request["document_base64"], str) or not isinstance(request["lineage"], dict)):
        raise ValueError
    # Compile the exact hashed bytes, bypassing pyc selection and file reload.
    # This path is fixed relative to the installed child, never request input.
    with os.fdopen(os.open(PROVIDER, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_PROVIDER:
            raise ValueError
        code = source.read(MAX_PROVIDER + 1)
        after = os.fstat(source.fileno())
    def identity(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
    if (len(code) != before.st_size or identity(before) != identity(after)
            or hashlib.sha256(code).hexdigest() != request["provider_sha256"]):
        raise ValueError
    module = types.ModuleType("_oday_native_manual_parser")
    sys.modules[module.__name__] = module
    exec(compile(code, str(PROVIDER), "exec"), module.__dict__)  # nosec B102: fixed installed path, exact attested bytes
    lineage = dict(request["lineage"])
    rows = lineage.get("correction_ids")
    if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != 2 for row in rows):
        raise ValueError
    lineage["correction_ids"] = tuple(tuple(row) for row in rows)
    binding = module.ManualParserLineage(**lineage)
    data = base64.b64decode(request["document_base64"], validate=True)
    result = module.parse_manual_evidence(data, binding).payloads()
    return {"nonce": request["nonce"], "provider_sha256": request["provider_sha256"], "payload": result}


def main():
    # Limits belong to this fixed engineering parser, not an approved SLO.
    # All authority and the shorter original command deadline remain external.
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if not 1 <= len(raw) <= MAX_INPUT:
            raise ValueError
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=closed, parse_constant=invalid)
        result = parse(request)
        reply = {"ok": True, "result": result}
        encoded = json.dumps(reply, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > MAX_OUTPUT:
            raise ValueError
    except Exception:
        # Never expose input bytes, exception messages or provider tracebacks.
        encoded = b'{"ok":false,"code":"NATIVE_IO_PARSER_INPUT_REJECTED","status":null}'
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
