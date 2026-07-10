from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationAsset:
    path: str
    sha256: str
    role: str


@dataclass(frozen=True)
class MigrationStep:
    revision: str
    path: str
    sha256: str
    assets: tuple[MigrationAsset, ...]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision_from_path(path: Path) -> str:
    return path.stem.split("_", 1)[0]


def _asset(path: Path, role: str) -> MigrationAsset:
    return MigrationAsset(path=path.as_posix(), sha256=_sha256(path), role=role)


def _referenced_sql_filenames(migration_file: Path) -> set[str]:
    tree = ast.parse(migration_file.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.endswith(".sql"):
                names.add(Path(value).name)
    return names


def _companion_sql_assets(migration_file: Path, migrations_root: Path) -> tuple[MigrationAsset, ...]:
    referenced = _referenced_sql_filenames(migration_file)
    return tuple(
        _asset(path, "sql")
        for path in sorted(migrations_root.glob("*.sql"))
        if path.name in referenced
    )


def _step_checksum(assets: tuple[MigrationAsset, ...]) -> str:
    payload = [asdict(asset) for asset in assets]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_migration_manifest_checksum(steps: tuple[MigrationStep, ...]) -> str:
    payload = [
        {
            "revision": step.revision,
            "path": step.path,
            "sha256": step.sha256,
            "assets": [asdict(asset) for asset in step.assets],
        }
        for step in steps
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def discover_migration_steps(migrations_dir: Path) -> tuple[MigrationStep, ...]:
    if not migrations_dir.exists():
        raise FileNotFoundError(f"migrations directory not found: {migrations_dir}")

    migrations_root = migrations_dir.parent
    steps: list[MigrationStep] = []
    for path in sorted(migrations_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        assets = (_asset(path, "alembic"), *_companion_sql_assets(path, migrations_root))
        revision = _revision_from_path(path)
        steps.append(
            MigrationStep(
                revision=revision,
                path=path.as_posix(),
                sha256=_step_checksum(assets),
                assets=assets,
            )
        )
    return tuple(steps)
