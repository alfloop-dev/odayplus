from __future__ import annotations

from scripts.e2e import seed_product_e2e_data


def test_web_readiness_retries_transient_disconnect(monkeypatch) -> None:
    attempts = iter((OSError("server closed startup connection"), None))

    class ReadyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    def open_url(_url: str, *, timeout: int):
        assert timeout == 10
        outcome = next(attempts)
        if outcome is not None:
            raise outcome
        return ReadyResponse()

    monkeypatch.setattr(seed_product_e2e_data, "urlopen", open_url)
    monkeypatch.setattr(seed_product_e2e_data.time, "sleep", lambda _seconds: None)

    seed_product_e2e_data.wait_for_http_url("http://127.0.0.1:3100")
