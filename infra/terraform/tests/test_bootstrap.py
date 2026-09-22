from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

BOOT_DIR = Path(__file__).resolve().parents[1] / "bootstrap"


class TerraformBootstrapContractTests(unittest.TestCase):
    def test_bootstrap_files_exist(self) -> None:
        required = ["main.tf", "variables.tf", "outputs.tf", "README.md", "bootstrap.sh"]
        for f in required:
            self.assertTrue((BOOT_DIR / f).is_file(), f"Missing required file: {f}")

    def test_bootstrap_governed_bucket_and_kms(self) -> None:
        main_tf = (BOOT_DIR / "main.tf").read_text(encoding="utf-8")
        self.assertIn('resource "google_storage_bucket" "terraform_state"', main_tf)
        self.assertIn('resource "google_kms_key_ring" "state_backend"', main_tf)
        self.assertIn('resource "google_kms_crypto_key" "state_backend"', main_tf)
        self.assertIn('public_access_prevention    = "enforced"', main_tf)
        self.assertIn("uniform_bucket_level_access = true", main_tf)
        self.assertIn("versioning {", main_tf)
        self.assertIn("retention_policy {", main_tf)
        self.assertIn("default_kms_key_name = google_kms_crypto_key.state_backend.id", main_tf)

    def test_bootstrap_destroy_guards_enforced(self) -> None:
        main_tf = (BOOT_DIR / "main.tf").read_text(encoding="utf-8")
        # Negative constraint: force_destroy must NOT be dynamically enabled for non-prod
        self.assertNotIn("force_destroy               = !local.is_prod", main_tf)
        self.assertIn("force_destroy               = false", main_tf)
        # Both KMS and State Bucket must have prevent_destroy = true
        self.assertEqual(main_tf.count("prevent_destroy = true"), 2)

    def test_two_phase_bootstrap_script_contract(self) -> None:
        script = (BOOT_DIR / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("-backend=false", script)
        self.assertIn("-migrate-state", script)
        self.assertIn("-backend-config=", script)
        self.assertIn("output -raw state_bucket_name", script)
        # Absolute path resolution & failure state preservation contract
        self.assertIn("RAW_VAR_FILE=", script)
        self.assertIn("VAR_FILE=", script)
        self.assertIn("BOOTSTRAP_SUCCESS=", script)
        self.assertIn("Preserving phase 1", script)
        self.assertIn("RECOVERY GUIDANCE", script)

    def test_bootstrap_declares_the_same_gcs_backend_contract_as_root(self) -> None:
        main_tf = (BOOT_DIR / "main.tf").read_text(encoding="utf-8")
        root_main_tf = (BOOT_DIR.parent / "main.tf").read_text(encoding="utf-8")
        self.assertIn('backend "gcs" {}', main_tf)
        self.assertIn('backend "gcs" {}', root_main_tf)

    def test_bootstrap_outputs_and_release_prefix_pattern(self) -> None:
        outputs_tf = (BOOT_DIR / "outputs.tf").read_text(encoding="utf-8")
        self.assertIn('output "state_bucket_name"', outputs_tf)
        self.assertIn('output "state_bucket_url"', outputs_tf)
        self.assertIn('output "state_kms_key_id"', outputs_tf)
        self.assertIn('output "staging_ephemeral_release_prefix_pattern"', outputs_tf)
        self.assertIn("oday-plus/staging/releases/{release_id}", outputs_tf)


TERRAFORM_STUB = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(os.environ["PROBE_ROOT"])
cwd = Path(sys.argv[1].split("=", 1)[1])
args = sys.argv[2:]
command = args[0]
stage = ("migrate" if "-migrate-state" in args else command)
if command == "output":
    stage += ":" + args[-1]
with (root / "calls.jsonl").open("a") as f:
    f.write(json.dumps({"stage": stage, "args": args, "cwd": str(cwd)}) + "\n")

def write_state(marker):
    (cwd / "terraform.tfstate").write_text(json.dumps({"marker": marker}))
    (cwd / "terraform.tfstate.backup").write_text(json.dumps({"marker": "backup"}))

scenario = os.environ["PROBE_SCENARIO"]
if stage == "apply":
    write_state("partial")
if stage == os.environ.get("PROBE_SIGNAL_STAGE"):
    # A descendant models a Terraform provider. A shell-only signal must be
    # forwarded to the whole owned job, then awaited before copying state.
    child_code = r'''
import os, signal, time
from pathlib import Path
root = Path(os.environ["PROBE_ROOT"])
def stop(sig, frame):
    (root / "descendant_cancelled").write_text(str(sig))
    raise SystemExit(0)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
(root / "descendant_ready").touch()
time.sleep(0.5)
(root / "descendant_completed").touch()
'''
    child = subprocess.Popen([sys.executable, "-c", child_code])
    def stop(sig, frame):
        child.wait(timeout=2)
        # Deliberately return success after cancellation: the shell must keep
        # its cancellation status instead of accepting the child's exit 0.
        if stage != "plan":
            write_state("flushed-after-signal")
        (root / "terraform_cancelled").write_text(str(sig))
        raise SystemExit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    deadline = time.monotonic() + 2
    while not (root / "descendant_ready").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("descendant did not initialize")
        time.sleep(0.005)
    sig = getattr(signal, os.environ["PROBE_SIGNAL"])
    if os.environ["PROBE_SIGNAL_TARGET"] == "group":
        os.killpg(os.getsid(0), sig)
    else:
        os.kill(os.getppid(), sig)
    child.wait(timeout=2)
    # Reproduce the reviewer's successful command after signalling its parent.
    time.sleep(0.02)
if scenario == "partial_apply" and stage == "apply":
    raise SystemExit(17)
if scenario == "migration_failure" and stage == "migrate":
    write_state("migration-progress")
    raise SystemExit(19)
if scenario == "output_failure" and stage == "output:backend_config_hcl_example":
    raise SystemExit(23)
if stage == "plan":
    var_file = next(a.split("=", 1)[1] for a in args if a.startswith("-var-file="))
    if not Path(var_file).is_absolute() or not Path(var_file).is_file():
        raise SystemExit(24)
if stage == "migrate":
    shutil_state = (cwd / "terraform.tfstate").read_bytes()
    (root / "remote.tfstate").write_bytes(shutil_state)
if stage == "output:state_bucket_name":
    print("offline-state-bucket", end="")
elif stage == "output:backend_config_hcl_example":
    env = os.environ["PROBE_ENVIRONMENT"]
    print('bucket = "offline-state-bucket"\nprefix = "oday-plus/' + env + '"', end="")
elif stage == "output:environment":
    # Match outputs.tf: no environment output exists.
    raise SystemExit(1)
"""


class TerraformBootstrapExecutionTests(unittest.TestCase):
    """Execute the real shell script with an offline, instrumented Terraform."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="bootstrap-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bootstrap = self.root / "bootstrap config"
        self.bootstrap.mkdir()
        for name in ["bootstrap.sh", "main.tf", "variables.tf", "outputs.tf"]:
            shutil.copy2(BOOT_DIR / name, self.bootstrap / name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        stub = self.bin / "terraform"
        stub.write_text(TERRAFORM_STUB)
        stub.chmod(0o755)
        self.temp_work = self.root / "temporary work"
        self.temp_work.mkdir()
        self.vars = self.root / "relative vars.tfvars"
        self.vars.write_text('environment = "dev"\n')

    def run_bootstrap(self, scenario="success", **settings):
        env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "TMPDIR": str(self.temp_work),
            "PROBE_ROOT": str(self.root),
            "PROBE_SCENARIO": scenario,
            "PROBE_ENVIRONMENT": "dev",
            **settings,
        }
        return subprocess.run(
            ["bash", str(self.bootstrap / "bootstrap.sh"), self.vars.name],
            cwd=self.root, env=env, text=True, capture_output=True,
            timeout=10, start_new_session=True,
        )

    def stages(self):
        return [json.loads(line)["stage"] for line in (self.root / "calls.jsonl").read_text().splitlines()]

    def assert_preserved(self, marker, phase1_marker=None):
        state = self.bootstrap / "terraform.tfstate"
        self.assertEqual(json.loads(state.read_text())["marker"], marker)
        self.assertEqual(json.loads(state.with_suffix(".tfstate.backup").read_text())["marker"], "backup")
        directories = list(self.temp_work.glob("oday-bootstrap.*"))
        self.assertEqual(len(directories), 1)
        self.assertEqual(
            json.loads((directories[0] / "terraform.tfstate").read_text())["marker"],
            phase1_marker or marker,
        )

    def test_partial_apply_preserves_state_and_original_exit(self):
        result = self.run_bootstrap("partial_apply")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(self.stages(), ["init", "plan", "apply"])
        self.assert_preserved("partial")
        self.assertNotIn("Completed Successfully", result.stdout)

    def test_failed_migration_preserves_recoverable_state(self):
        result = self.run_bootstrap("migration_failure")
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assert_preserved("migration-progress", phase1_marker="partial")
        self.assertNotIn("Completed Successfully", result.stdout)

    def test_backend_output_failure_is_not_a_staging_fallback(self):
        result = self.run_bootstrap("output_failure")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertNotIn("migrate", self.stages())
        self.assert_preserved("partial")
        self.assertNotIn("Completed Successfully", result.stdout)

    def test_successful_migration_uses_actual_environment_and_cleans_local_state(self):
        for environment in ["dev", "staging", "prod"]:
            with self.subTest(environment=environment):
                result = self.run_bootstrap(PROBE_ENVIRONMENT=environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'prefix = "oday-plus/{environment}"', result.stdout)
                self.assertTrue((self.root / "remote.tfstate").is_file())
                self.assertFalse((self.bootstrap / "terraform.tfstate").exists())
                self.assertFalse((self.bootstrap / "terraform.tfstate.backup").exists())
                self.assertEqual(list(self.temp_work.iterdir()), [])

    def test_cancellation_stops_the_job_before_state_preservation(self):
        for stage in ["plan", "apply", "migrate"]:
            for signal_name, expected in [("SIGINT", 130), ("SIGTERM", 143)]:
                for target in ["shell", "group"]:
                    with self.subTest(stage=stage, signal=signal_name, target=target):
                        # Each signal case needs its own state and event log.
                        case = type(self)("test_partial_apply_preserves_state_and_original_exit")
                        case.setUp()
                        try:
                            result = case.run_bootstrap(
                                PROBE_SIGNAL_STAGE=stage, PROBE_SIGNAL=signal_name,
                                PROBE_SIGNAL_TARGET=target,
                            )
                            self.assertEqual(result.returncode, expected, result.stderr)
                            self.assertEqual(case.stages()[-1], stage)
                            self.assertTrue((case.root / "terraform_cancelled").is_file())
                            self.assertTrue((case.root / "descendant_cancelled").is_file())
                            self.assertFalse((case.root / "descendant_completed").exists())
                            self.assertNotIn("Completed Successfully", result.stdout)
                            if stage != "plan":
                                case.assert_preserved(
                                    "flushed-after-signal",
                                    phase1_marker="partial" if stage == "migrate" else None,
                                )
                            else:
                                self.assertEqual(len(list(case.temp_work.iterdir())), 1)
                        finally:
                            case.doCleanups()


if __name__ == "__main__":
    unittest.main()
