from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "infra" / "docker" / "Dockerfile.api"


def test_api_image_contains_production_model_release_runtime() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    for source in ("models", "modules", "pipelines", "scripts", "shared"):
        assert f"COPY {source} ./{source}" in dockerfile

    for dependency in (
        "google-cloud-storage",
        "lightgbm",
        "lifelines",
        "mlflow",
        "psycopg[binary,pool]",
    ):
        assert dependency in dockerfile
