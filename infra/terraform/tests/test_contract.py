from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "terraform_contract_validator",
    ROOT / "validate_contract.py",
)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class TerraformProductionContractTests(unittest.TestCase):
    def test_repository_contract_passes(self) -> None:
        self.assertEqual(validator.validate(ROOT), [])

    def test_unbalanced_hcl_is_rejected(self) -> None:
        self.assertFalse(validator._balanced_hcl('resource "x" "y" {'))
        self.assertTrue(
            validator._balanced_hcl(
                'resource "x" "y" { value = "${ignored}" } # ignored {'
            )
        )

    def test_missing_resource_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            database = copy_root / "database.tf"
            database.write_text(
                database.read_text(encoding="utf-8").replace("POSTGRES_16", "POSTGRES_15"),
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("POSTGRES_16" in error for error in errors))

    def test_plaintext_database_url_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copy_root = Path(directory)
            for relative in validator.REQUIRED_FILES:
                source = ROOT / relative
                destination = copy_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            outputs = copy_root / "outputs.tf"
            outputs.write_text(
                outputs.read_text(encoding="utf-8")
                + '\noutput "bad" { value = random_password.database.result }\n',
                encoding="utf-8",
            )
            errors = validator.validate(copy_root)
            self.assertTrue(any("random_password.database.result" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
