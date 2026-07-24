from __future__ import annotations

import json
import os

from infra.mlflow.runtime import MlflowServerSettings


def main() -> None:
    settings = MlflowServerSettings.from_environment()
    print(
        json.dumps(
            {
                "event": "mlflow.production_start",
                **settings.redacted_summary(),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    os.execvp(settings.server_command()[0], settings.server_command())


if __name__ == "__main__":
    main()
