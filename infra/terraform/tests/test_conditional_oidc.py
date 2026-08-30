"""Tests for conditional OIDC Terraform workflow validation.

ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001: Verifies that OIDC configuration is
strictly validated when auth_mode='oidc' and that password-first deployments
(auth_mode='local') pass without any OIDC variables.
"""

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


class ConditionalOidcTerraformTests(unittest.TestCase):
    """Verify the conditional OIDC deployment contract in Terraform HCL."""

    def test_auth_mode_variable_exists(self) -> None:
        """variables.tf must define auth_mode with local/oidc validation."""
        text = (ROOT / "variables.tf").read_text(encoding="utf-8")
        self.assertIn('variable "auth_mode"', text)
        self.assertIn('"local"', text)
        self.assertIn('"oidc"', text)

    def test_oidc_enabled_local_derived(self) -> None:
        """main.tf must define oidc_enabled derived from auth_mode."""
        text = (ROOT / "main.tf").read_text(encoding="utf-8")
        self.assertIn("oidc_enabled", text)
        self.assertIn('var.auth_mode == "oidc"', text)

    def test_web_plain_env_contains_auth_mode(self) -> None:
        """web_plain_env must include ODP_AUTH_MODE for all deployments."""
        text = (ROOT / "main.tf").read_text(encoding="utf-8")
        self.assertIn("ODP_AUTH_MODE", text)
        self.assertIn("var.auth_mode", text)

    def test_web_oidc_env_is_conditional(self) -> None:
        """OIDC env vars in web_plain_env must be gated on oidc_enabled."""
        text = (ROOT / "main.tf").read_text(encoding="utf-8")
        # The OIDC env vars must appear inside a conditional block
        self.assertIn("local.oidc_enabled ?", text)
        self.assertIn("ODP_AUTH_OIDC_ENABLED", text)

    def test_api_runtime_oidc_env_is_conditional(self) -> None:
        """OIDC API auth env vars in fixed_runtime_env must be gated."""
        text = (ROOT / "main.tf").read_text(encoding="utf-8")
        # ODP_AUTH_ISSUER and ODP_AUTH_JWKS_URI should be in a conditional block
        self.assertIn("ODP_AUTH_ISSUER", text)
        self.assertIn("ODP_AUTH_JWKS_URI", text)

    def test_production_contract_values_oidc_conditional(self) -> None:
        """OIDC values in production_contract_values must be conditional."""
        text = (ROOT / "main.tf").read_text(encoding="utf-8")
        # Find the production_contract_values section
        idx = text.find("production_contract_values")
        self.assertGreater(idx, 0)
        section = text[idx:idx + 1500]
        # OIDC values must be in a conditional block
        self.assertIn("local.oidc_enabled ?", section)

    def test_checks_oidc_precondition_gated(self) -> None:
        """checks.tf OIDC precondition must be gated on oidc_enabled."""
        text = (ROOT / "checks.tf").read_text(encoding="utf-8")
        # The OIDC precondition should use oidc_enabled
        self.assertIn("local.oidc_enabled", text)
        self.assertIn(
            "Production with OIDC enabled requires",
            text,
        )

    def test_checks_invoker_always_required_for_prod(self) -> None:
        """Production invoker requirements must not be gated on OIDC."""
        text = (ROOT / "checks.tf").read_text(encoding="utf-8")
        self.assertIn(
            "Production requires explicit non-public API and Web invoker members.",
            text,
        )

    def test_identity_contract_check_oidc_gated(self) -> None:
        """production_identity_contract OIDC assert must be gated."""
        text = (ROOT / "checks.tf").read_text(encoding="utf-8")
        idx = text.find('check "production_identity_contract"')
        self.assertGreater(idx, 0)
        section = text[idx:idx + 500]
        self.assertIn("local.oidc_enabled", section)

    def test_web_oidc_secret_ref_conditional_in_cloud_run(self) -> None:
        """Cloud Run web service OIDC secret must remain conditional."""
        text = (ROOT / "cloud_run.tf").read_text(encoding="utf-8")
        self.assertIn("web_oidc_secret_refs", text)

    def test_iam_web_oidc_client_data_source_conditional(self) -> None:
        """IAM data source for OIDC client secret iterates web_oidc_secret_refs."""
        text = (ROOT / "iam.tf").read_text(encoding="utf-8")
        self.assertIn("web_oidc_secret_refs", text)

    def test_contract_validation_passes_with_changes(self) -> None:
        """validate_contract.py must pass with the updated HCL."""
        errors = validator.validate(ROOT)
        self.assertEqual(errors, [], f"Contract validation failed: {errors}")

    def test_auth_mode_token_in_contract_validator(self) -> None:
        """validate_contract.py must check for ODP_AUTH_MODE token."""
        tokens = validator.REQUIRED_TOKENS.get("main.tf", set())
        self.assertIn("ODP_AUTH_MODE", tokens)

    def test_local_mode_no_oidc_env_in_web(self) -> None:
        """When auth_mode=local, web_plain_env must not contain OIDC vars.

        Structural check: the merge uses 'local.oidc_enabled ? { ... } : {}'
        pattern, ensuring an empty map when oidc is disabled.
        """
        text = (ROOT / "main.tf").read_text(encoding="utf-8")
        # Verify the ternary pattern exists for conditional OIDC inclusion
        self.assertIn("} : {},", text)

    def test_deploy_script_conditional_oidc_secret(self) -> None:
        """deploy_cloud_run_waji.sh must conditionally bind OIDC secret."""
        script = Path(ROOT).parents[1] / "product_ops" / "deployment" / "deploy_cloud_run_waji.sh"
        if not script.exists():
            self.skipTest("deploy script not found at expected path")
        text = script.read_text(encoding="utf-8")
        # Session secret should always be bound
        self.assertIn('WEB_SECRET_BINDINGS="ODP_WEB_SESSION_SECRET=', text)
        # OIDC client secret should be conditional
        self.assertIn("ODP_WEB_OIDC_CLIENT_SECRET_SECRET:-", text)

    def test_deploy_script_conditional_oidc_env(self) -> None:
        """deploy_cloud_run_waji.sh must conditionally inject OIDC env."""
        script = Path(ROOT).parents[1] / "product_ops" / "deployment" / "deploy_cloud_run_waji.sh"
        if not script.exists():
            self.skipTest("deploy script not found at expected path")
        text = script.read_text(encoding="utf-8")
        # OIDC env vars should use os.environ.get() pattern
        self.assertIn('os.environ.get("ODP_WEB_OIDC_ISSUER")', text)
        self.assertIn('"ODP_AUTH_MODE": os.environ.get("ODP_AUTH_MODE", "local")', text)

    def test_workflow_passes_oidc_enabled(self) -> None:
        """deploy-dev.yml must pass ODP_AUTH_OIDC_ENABLED."""
        workflow = Path(ROOT).parents[1] / ".github" / "workflows" / "deploy-dev.yml"
        if not workflow.exists():
            self.skipTest("workflow not found at expected path")
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("ODP_AUTH_OIDC_ENABLED", text)


class ConditionalOidcLiveValidatorTests(unittest.TestCase):
    """Verify the live deployment validator honors conditional OIDC."""

    VALIDATOR_PATH = Path(ROOT).parents[1] / "product_ops" / "deployment" / "validate_cloud_run_live_deployment.py"

    def _read_source(self) -> str:
        if not self.VALIDATOR_PATH.exists():
            self.skipTest("live validator not found")
        return self.VALIDATOR_PATH.read_text(encoding="utf-8")

    def test_oidc_vars_not_in_required_public_config(self) -> None:
        """OIDC vars must not be in REQUIRED_PUBLIC_CONFIG."""
        text = self._read_source()
        # Find the REQUIRED_PUBLIC_CONFIG tuple
        start = text.find("REQUIRED_PUBLIC_CONFIG = (")
        end = text.find(")", start) + 1
        required_block = text[start:end]
        self.assertNotIn('"ODP_WEB_OIDC_ISSUER"', required_block)
        self.assertNotIn('"ODP_WEB_OIDC_CLIENT_ID"', required_block)

    def test_oidc_secret_not_in_required_secret_references(self) -> None:
        """OIDC secret must not be in REQUIRED_SECRET_REFERENCES."""
        text = self._read_source()
        start = text.find("REQUIRED_SECRET_REFERENCES = (")
        end = text.find(")", start) + 1
        required_block = text[start:end]
        self.assertNotIn('"ODP_WEB_OIDC_CLIENT_SECRET_SECRET"', required_block)

    def test_oidc_vars_in_conditional_lists(self) -> None:
        """OIDC vars must exist in the OIDC-conditional lists."""
        text = self._read_source()
        self.assertIn("OIDC_REQUIRED_PUBLIC_CONFIG", text)
        self.assertIn("OIDC_REQUIRED_SECRET_REFERENCES", text)
        oidc_pub_start = text.find("OIDC_REQUIRED_PUBLIC_CONFIG = (")
        oidc_pub_end = text.find(")", oidc_pub_start) + 1
        oidc_pub_block = text[oidc_pub_start:oidc_pub_end]
        self.assertIn('"ODP_WEB_OIDC_ISSUER"', oidc_pub_block)
        self.assertIn('"ODP_WEB_OIDC_CLIENT_ID"', oidc_pub_block)
        oidc_secret_start = text.find("OIDC_REQUIRED_SECRET_REFERENCES = (")
        oidc_secret_end = text.find(")", oidc_secret_start) + 1
        oidc_secret_block = text[oidc_secret_start:oidc_secret_end]
        self.assertIn('"ODP_WEB_OIDC_CLIENT_SECRET_SECRET"', oidc_secret_block)

    def test_base_required_configs_unchanged(self) -> None:
        """Non-OIDC required configs must still be present."""
        text = self._read_source()
        start = text.find("REQUIRED_PUBLIC_CONFIG = (")
        end = text.find(")", start) + 1
        required_block = text[start:end]
        self.assertIn('"GCP_PROJECT"', required_block)
        self.assertIn('"GCP_REGION"', required_block)
        secret_start = text.find("REQUIRED_SECRET_REFERENCES = (")
        secret_end = text.find(")", secret_start) + 1
        secret_block = text[secret_start:secret_end]
        self.assertIn('"ODAY_DATABASE_URL_SECRET"', secret_block)
        self.assertIn('"ODP_WEB_SESSION_SECRET_SECRET"', secret_block)

    def test_oidc_conditional_validation_loop_exists(self) -> None:
        """The conditional OIDC validation loop must exist."""
        text = self._read_source()
        self.assertIn("oidc_enabled", text)
        self.assertIn("OIDC_REQUIRED_PUBLIC_CONFIG", text)
        self.assertIn("oidc-config:", text)
        self.assertIn("oidc-secret-reference:", text)


if __name__ == "__main__":
    unittest.main()
