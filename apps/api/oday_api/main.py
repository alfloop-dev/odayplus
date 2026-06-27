from __future__ import annotations

from typing import Any


def health_payload() -> dict[str, str]:
    return {"status": "ok", "service": "oday-api"}


try:
    from fastapi import FastAPI
except ModuleNotFoundError:  # pragma: no cover - dependency added by backend task
    app: Any = None
else:
    app = FastAPI(title="ODay Plus API", version="0.1.0")

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        return health_payload()
