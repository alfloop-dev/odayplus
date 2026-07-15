"""Shared fixtures for security tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def temp_env(tmp_path: Path) -> dict[str, Path]:
    status_file = tmp_path / "ai-status.json"
    config_file = tmp_path / "config.json"
    policy_file = tmp_path / "policy.json"

    # Default policy setup
    policy_file.write_text(
        json.dumps(
            {
                "required_status_checks": ["orchestrator", "product", "product-e2e-gate"],
                "enforce_admins": True,
                "required_approving_review_count": 1,
            }
        ),
        encoding="utf-8",
    )

    # Default config setup
    config_file.write_text(
        json.dumps(
            {
                "github_bus": {
                    "reviewers": {
                        "Codex": ["codex-bot", "codex-admin"],
                        "Claude": ["claude-bot"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    # Default task status registry setup
    status_file.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "ODP-OC-R5-012",
                        "status": "review_approved",
                        "reviewer": "Codex",
                        "owner": "Antigravity",
                    },
                    {
                        "id": "ODP-OC-R5-011",
                        "status": "review",
                        "reviewer": "Claude",
                        "owner": "Claude",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    return {
        "status": status_file,
        "config": config_file,
        "policy": policy_file,
    }
