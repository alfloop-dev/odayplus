"""Fixed test-only storage process; no provider, credential or network access."""
import json
import math
import sys


def main():
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise SystemExit(2)
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {"operation", "timeout_seconds"}
            or value["operation"] != "synthetic_parser_storage"
            or type(value["timeout_seconds"]) not in {int, float}
            or not math.isfinite(value["timeout_seconds"])
            or not 0 < value["timeout_seconds"] <= 10):
        raise SystemExit(2)
    sys.stdout.write('{"ok":true,"result":{}}\n')


if __name__ == "__main__":
    main()
