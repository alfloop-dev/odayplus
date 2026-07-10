from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import yaml

from models.shared_ml.model_card import ModelCard, ModelCardApproval, ModelRiskLevel


class LocalModelArtifactStore:
    """Local file-based artifact store for ML models, handling Model Cards and metadata."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else Path("/tmp/model_artifacts")

    def save_model_card(self, model_card: ModelCard, artifact_uri: str | None = None) -> str:
        """Saves a model card as a YAML file inside the model validation artifact directory.

        Args:
            model_card: The ModelCard instance to save.
            artifact_uri: Optional base directory to store the card in. If not provided,
              defaults to self.base_dir / model_name / model_version.

        Returns:
            The absolute path to the saved model card file.
        """
        if artifact_uri:
            path_str = artifact_uri
            if path_str.startswith("file://"):
                path_str = path_str[7:]
            target_dir = Path(path_str) / "validation"
        else:
            target_dir = (
                self.base_dir
                / model_card.model_name
                / model_card.model_version
                / "validation"
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "model_card.yaml"

        data = model_card.to_dict()
        with open(target_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

        return str(target_file)

    def load_model_card(
        self,
        model_name: str,
        version: str,
        artifact_uri: str | None = None,
    ) -> ModelCard | None:
        """Loads a model card from the validation directory of the model artifact.

        Args:
            model_name: The name of the model.
            version: The version of the model.
            artifact_uri: Optional base directory containing the model.

        Returns:
            The ModelCard instance if found, otherwise None.
        """
        if artifact_uri:
            path_str = artifact_uri
            if path_str.startswith("file://"):
                path_str = path_str[7:]
            target_file = Path(path_str) / "validation" / "model_card.yaml"
        else:
            target_file = self.base_dir / model_name / version / "validation" / "model_card.yaml"

        if not target_file.exists():
            return None

        with open(target_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        approvals = [
            ModelCardApproval(
                approver=app["approver"],
                role=app["role"],
                decision=app.get("decision", "approved"),
                approved_at=datetime.fromisoformat(app["approved_at"]),
            )
            for app in data.get("approvals", [])
        ]

        # Handle datetime parsing for created_at
        created_at_val = data.get("created_at")
        if isinstance(created_at_val, str):
            created_at = datetime.fromisoformat(created_at_val)
        else:
            created_at = datetime.now()

        return ModelCard(
            model_name=data["model_name"],
            model_version=data["model_version"],
            owner=data["owner"],
            risk_level=ModelRiskLevel(data["risk_level"]),
            intended_use=data["intended_use"],
            not_intended_use=data["not_intended_use"],
            dataset_snapshot_id=data["dataset_snapshot_id"],
            validation_run_id=data["validation_run_id"],
            feature_set_id=data["feature_set_id"],
            label_set_id=data["label_set_id"],
            training_period=data["training_period"],
            validation_period=data["validation_period"],
            algorithm=data["algorithm"],
            baseline=data["baseline"],
            metrics_summary=data["metrics_summary"],
            segment_metrics=data.get("segment_metrics", []),
            calibration_summary=data.get("calibration_summary", {}),
            explainability_method=data.get("explainability_method", "not_applicable"),
            limitations=data.get("limitations", []),
            known_biases=data.get("known_biases", []),
            privacy_review=data.get("privacy_review", "PASSED"),
            security_review=data.get("security_review", "PASSED"),
            release_status=data.get("release_status", "DEV"),
            rollback_conditions=data.get("rollback_conditions", []),
            approvals=approvals,
            created_at=created_at,
        )


__all__ = ["LocalModelArtifactStore"]
