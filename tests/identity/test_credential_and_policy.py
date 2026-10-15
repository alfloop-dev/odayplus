"""ODP-WEB-LOCAL-IDENTITY-CORE-001 測試套件：T01–T04, T08

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §6, §9, §10

T01: Argon2id 參數符合 §6.1；PHC 往返
T02: rehash-on-verify 升級
T03: 密碼政策（長度、NFKC、弱密碼、近似 username）
T04: 帳號不存在 vs 密碼錯誤的回應與時序（dummy verify）
T08: identity schema expand migration 可重跑、可回退為 no-op
"""
from __future__ import annotations

import re
import time

import pytest

from shared.identity.credential_service import (
    CURRENT_POLICY,
    Argon2Policy,
    CredentialService,
)
from shared.identity.password_policy import (
    PasswordPolicy,
    PasswordPolicyError,
)

# ============================================================================
# T01: Argon2id 參數符合 §6.1；PHC 往返
# ============================================================================

class TestT01Argon2idParams:
    """驗證 Argon2id 雜湊參數符合契約 §6.1 規格。"""

    def setup_method(self) -> None:
        self.svc = CredentialService()

    def test_default_policy_meets_contract(self) -> None:
        """CURRENT_POLICY 的參數必須 ≥ 契約下限。"""
        assert CURRENT_POLICY.memory_cost >= 65536, "memory_cost must be ≥ 65536 KiB"
        assert CURRENT_POLICY.time_cost >= 3, "time_cost must be ≥ 3"
        assert CURRENT_POLICY.parallelism == 1, "parallelism must be 1"
        assert CURRENT_POLICY.hash_len == 32, "hash_len must be 32 bytes"
        assert CURRENT_POLICY.salt_len >= 16, "salt_len must be ≥ 16 bytes"

    def test_phc_format_roundtrip(self) -> None:
        """PHC 編碼字串格式正確且可往返。"""
        password = "test-password-12345"
        phc_hash = self.svc.hash_password(password)

        # PHC 格式檢查
        assert phc_hash.startswith("$argon2id$"), f"Must start with $argon2id$: {phc_hash[:30]}"
        assert "$v=19$" in phc_hash, "Must contain version v=19"

        # 參數可從 PHC 萃取
        params = CredentialService.extract_params_from_phc(phc_hash)
        assert params["algorithm"] == "argon2id"
        assert params["memory_cost"] >= 65536
        assert params["time_cost"] >= 3
        assert params["parallelism"] == 1

    def test_verify_correct_password(self) -> None:
        """正確密碼驗證通過。"""
        password = "correct-horse-battery-staple"
        phc_hash = self.svc.hash_password(password)
        assert self.svc.verify_password(phc_hash, password) is True

    def test_verify_wrong_password_raises(self) -> None:
        """錯誤密碼驗證拋出 VerifyMismatchError。"""
        from argon2.exceptions import VerifyMismatchError  # type: ignore[import-untyped]

        password = "correct-password-1234"
        phc_hash = self.svc.hash_password(password)
        with pytest.raises(VerifyMismatchError):
            self.svc.verify_password(phc_hash, "wrong-password-5678")

    def test_each_hash_has_unique_salt(self) -> None:
        """每次雜湊必須使用不同的 salt。"""
        password = "same-password-12345"
        h1 = self.svc.hash_password(password)
        h2 = self.svc.hash_password(password)
        assert h1 != h2, "Each hash must use a unique CSPRNG salt"
        # 但都能驗證通過
        assert self.svc.verify_password(h1, password) is True
        assert self.svc.verify_password(h2, password) is True

    def test_params_dict_contains_policy_version(self) -> None:
        """參數字典包含版本號以支援升級。"""
        params = CURRENT_POLICY.to_params_dict()
        assert "policy_version" in params
        assert params["policy_version"] == 1


# ============================================================================
# T02: rehash-on-verify 升級
# ============================================================================

class TestT02RehashOnVerify:
    """驗證舊參數雜湊在成功登入後被自動 rehash。"""

    def test_needs_rehash_with_old_params(self) -> None:
        """使用低於當前政策的參數，needs_rehash 必須為真。"""
        # 使用較低參數建立舊雜湊
        old_policy = Argon2Policy(
            version=0,
            memory_cost=32768,  # 低於 65536
            time_cost=2,        # 低於 3
            parallelism=1,
            hash_len=32,
            salt_len=16,
        )
        old_svc = CredentialService(policy=old_policy)
        password = "upgrade-test-password"
        old_hash = old_svc.hash_password(password)

        # 使用新政策檢查
        new_svc = CredentialService()
        assert new_svc.needs_rehash(old_hash) is True

    def test_no_rehash_with_current_params(self) -> None:
        """使用當前政策的參數，needs_rehash 為假。"""
        svc = CredentialService()
        password = "current-params-password"
        current_hash = svc.hash_password(password)
        assert svc.needs_rehash(current_hash) is False

    def test_verify_and_rehash_upgrades(self) -> None:
        """verify_and_rehash 在參數過舊時回傳新雜湊。"""
        old_policy = Argon2Policy(
            version=0,
            memory_cost=32768,
            time_cost=2,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        )
        old_svc = CredentialService(policy=old_policy)
        password = "rehash-upgrade-password"
        old_hash = old_svc.hash_password(password)

        # 用新政策的 svc 做 verify_and_rehash
        new_svc = CredentialService()
        valid, new_hash = new_svc.verify_and_rehash(old_hash, password)

        assert valid is True
        assert new_hash is not None, "應該產生新雜湊"
        assert new_hash != old_hash, "新雜湊應與舊的不同"
        assert new_svc.needs_rehash(new_hash) is False, "新雜湊不應再需要 rehash"

    def test_verify_and_rehash_no_upgrade_needed(self) -> None:
        """verify_and_rehash 在參數已是最新時不產生新雜湊。"""
        svc = CredentialService()
        password = "no-upgrade-needed-pw"
        current_hash = svc.hash_password(password)

        valid, new_hash = svc.verify_and_rehash(current_hash, password)
        assert valid is True
        assert new_hash is None, "不應產生新雜湊"


# ============================================================================
# T03: 密碼政策（長度、NFKC、弱密碼、近似 username）
# ============================================================================

class TestT03PasswordPolicy:
    """驗證密碼政策檢查器（Contract §6.2）。"""

    def setup_method(self) -> None:
        self.policy = PasswordPolicy()

    def test_too_short(self) -> None:
        """短於 12 字元的密碼被拒絕。"""
        result = self.policy.validate("short")
        assert not result.valid
        codes = {v.code for v in result.violations}
        assert PasswordPolicyError.TOO_SHORT in codes

    def test_exactly_12_chars_accepted(self) -> None:
        """恰好 12 字元的密碼通過長度檢查。"""
        result = self.policy.validate("abcdefg12345")
        # 可能因弱密碼或其他原因失敗，但不應因長度失敗
        codes = {v.code for v in result.violations}
        assert PasswordPolicyError.TOO_SHORT not in codes

    def test_too_long(self) -> None:
        """超過 1024 字元的密碼被拒絕。"""
        result = self.policy.validate("x" * 1025)
        assert not result.valid
        codes = {v.code for v in result.violations}
        assert PasswordPolicyError.TOO_LONG in codes

    def test_nfkc_normalization(self) -> None:
        """Unicode NFKC 正規化：ﬁ → fi 等。"""
        # ﬁ (U+FB01) 在 NFKC 下正規化為 "fi"
        normalized = PasswordPolicy.normalize("ﬁrewall")
        assert normalized == "firewall"

    def test_weak_password_rejected(self) -> None:
        """弱密碼清單中的密碼被拒絕。"""
        result = self.policy.validate("password1234")
        assert not result.valid
        codes = {v.code for v in result.violations}
        assert PasswordPolicyError.IN_WEAK_LIST in codes

    def test_weak_password_case_insensitive(self) -> None:
        """弱密碼比對不區分大小寫。"""
        result = self.policy.validate("PASSWORD1234")
        assert not result.valid
        codes = {v.code for v in result.violations}
        assert PasswordPolicyError.IN_WEAK_LIST in codes

    def test_similar_to_username_rejected(self) -> None:
        """與 username 高度相似的密碼被拒絕。"""
        result = self.policy.validate(
            "johnsmith1234",
            username="johnsmith",
        )
        assert not result.valid
        codes = {v.code for v in result.violations}
        assert PasswordPolicyError.SIMILAR_TO_USERNAME in codes

    def test_similar_to_email_rejected(self) -> None:
        """與 email local part 高度相似的密碼被拒絕。"""
        result = self.policy.validate(
            "alicewang1234",
            email="alicewang@example.com",
        )
        assert not result.valid
        codes = {v.code for v in result.violations}
        assert PasswordPolicyError.SIMILAR_TO_EMAIL in codes

    def test_good_password_accepted(self) -> None:
        """足夠強的密碼通過所有檢查。"""
        result = self.policy.validate(
            "Tr0ub4dor&3-correct-horse",
            username="admin",
            email="admin@example.com",
        )
        assert result.valid
        assert len(result.violations) == 0

    def test_no_composition_rules(self) -> None:
        """不強制組成規則：純小寫 12+ 字元的非弱密碼應通過。"""
        result = self.policy.validate(
            "thistownwasneverforyou",
            username="bob",
            email="bob@example.com",
        )
        assert result.valid, f"不應強制組成規則: {result.violations}"


# ============================================================================
# T04: 帳號不存在 vs 密碼錯誤的回應與時序
# ============================================================================

class TestT04DummyVerify:
    """驗證 dummy verify 防止帳號枚舉時序攻擊。"""

    def setup_method(self) -> None:
        self.svc = CredentialService()

    def test_dummy_verify_runs_without_error(self) -> None:
        """dummy_verify 執行不拋出例外。"""
        # 應該完成而不出錯
        self.svc.dummy_verify()

    def test_dummy_verify_has_comparable_cost(self) -> None:
        """dummy_verify 的執行時間與正常驗證應在同一量級。

        注意：這是一個 best-effort timing test。在 CI 環境中
        時間可能不穩定，但趨勢應該一致。
        """
        password = "timing-test-password"
        phc_hash = self.svc.hash_password(password)

        # 測量正常驗證（密碼正確）
        start = time.perf_counter()
        self.svc.verify_password(phc_hash, password)
        normal_time = time.perf_counter() - start

        # 測量 dummy verify
        start = time.perf_counter()
        self.svc.dummy_verify()
        dummy_time = time.perf_counter() - start

        # dummy_verify 應在 normal 的 0.1x ~ 10x 範圍內
        # （寬鬆界限以適應 CI 環境的變異）
        ratio = dummy_time / normal_time if normal_time > 0 else 1.0
        assert 0.1 < ratio < 10, (
            f"dummy_verify 時間 ({dummy_time:.4f}s) 與正常驗證 "
            f"({normal_time:.4f}s) 比率 {ratio:.2f} 超出合理範圍"
        )

    def test_extract_params_invalid_hash(self) -> None:
        """非 Argon2id PHC 字串應拋出 ValueError。"""
        with pytest.raises(ValueError, match="Not a valid Argon2id"):
            CredentialService.extract_params_from_phc("$bcrypt$...")


# ============================================================================
# T08: identity schema expand migration 可重跑、可回退為 no-op
# ============================================================================

class TestT08MigrationIdempotency:
    """驗證 migration SQL 的冪等性（無需實際資料庫）。

    此測試驗證 SQL 檔案的結構正確性：
    - 所有 CREATE 語句使用 IF NOT EXISTS
    - 所有 DROP 語句使用 IF EXISTS
    - 包含所有契約表
    """

    def setup_method(self) -> None:
        import os
        import subprocess
        # Use git to find the repo root reliably across worktrees
        repo_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
        ).strip()
        sql_path = os.path.join(
            repo_root,
            "infra", "db", "migrations", "000011_identity_schema.sql",
        )
        with open(sql_path, encoding="utf-8") as f:
            self.sql = f.read()
        self._repo_root = repo_root

    def test_creates_identity_schema(self) -> None:
        """SQL 包含建立 identity schema。"""
        assert "CREATE SCHEMA IF NOT EXISTS identity" in self.sql

    def test_all_contract_tables_present(self) -> None:
        """SQL 包含契約規定的所有 8 張表。"""
        expected_tables = [
            "identity.accounts",
            "identity.password_credentials",
            "identity.account_roles",
            "identity.account_scopes",
            "identity.sessions",
            "identity.invitations",
            "identity.federated_identities",
            "identity.login_attempts",
        ]
        for table in expected_tables:
            assert table in self.sql, f"Missing contract table: {table}"

    def test_all_creates_are_idempotent(self) -> None:
        """所有 CREATE TABLE 語句使用 IF NOT EXISTS。"""
        # 找出所有 CREATE TABLE 行
        create_lines = re.findall(
            r"CREATE TABLE\b.*identity\.\w+",
            self.sql,
            re.IGNORECASE,
        )
        for line in create_lines:
            assert "IF NOT EXISTS" in line.upper(), (
                f"CREATE TABLE must use IF NOT EXISTS: {line}"
            )

    def test_accounts_status_constraint(self) -> None:
        """accounts.status 約束包含契約規定的四個值。"""
        assert "'invited'" in self.sql
        assert "'active'" in self.sql
        assert "'disabled'" in self.sql
        assert "'locked'" in self.sql

    def test_sessions_provider_constraint(self) -> None:
        """sessions.provider 約束包含契約規定的兩個值。"""
        assert "'local_password'" in self.sql
        assert "'oidc'" in self.sql

    def test_password_credentials_algorithm_constraint(self) -> None:
        """password_credentials.algorithm 固定為 argon2id。"""
        assert "'argon2id'" in self.sql

    def test_unique_constraints(self) -> None:
        """包含契約規定的唯一約束。"""
        # tenant + lower(username)
        assert "lower(username)" in self.sql
        # tenant + lower(email)
        assert "lower(email)" in self.sql
        # federated_identities (issuer, subject) UNIQUE
        assert "UNIQUE (issuer, subject)" in self.sql

    def test_alembic_version_exists(self) -> None:
        """Alembic revision 檔案存在且指向正確的 SQL。"""
        import os
        version_path = os.path.join(
            self._repo_root,
            "infra", "db", "migrations", "versions", "0004_identity_schema.py",
        )
        assert os.path.exists(version_path), \
            "Alembic version 0004_identity_schema.py must exist"


class TestTimingDummyInitialization:
    """Separate owning controls; original T01–T08 bodies above are unchanged.

    All acceptance positives use real Argon2. Profiles count genuine calls;
    failure doubles below are fault injection, never crypto acceptance evidence.
    """

    def setup_method(self) -> None:
        from shared.identity import credential_service as credentials

        credentials._reset_timing_dummy()

    def teardown_method(self) -> None:
        from shared.identity import credential_service as credentials

        credentials._reset_timing_dummy()

    def test_one_initialization_but_every_dummy_verification_is_real(self) -> None:
        import sys

        from argon2 import _password_hasher, extract_parameters, low_level

        from shared.identity import credential_service as credentials

        counts = {"hash": 0, "verify": 0}
        hash_code = low_level.hash_secret.__code__
        verify_code = low_level.verify_secret.__code__

        def observe(frame, event, arg):
            if event == "call":
                if frame.f_code is hash_code:
                    counts["hash"] += 1
                elif frame.f_code is verify_code:
                    counts["verify"] += 1

        previous = sys.getprofile()
        try:
            sys.setprofile(observe)
            services = [CredentialService() for _ in range(4)]
            for service in services:
                service.dummy_verify()
                service.dummy_verify()
            assert counts == {"hash": 1, "verify": 8}
            assert len({id(service._hasher) for service in services}) == 4
            assert len({service._dummy_hash for service in services}) == 1
            assert credentials._dummy_artifact == (CURRENT_POLICY, services[0]._dummy_hash)
            params = extract_parameters(services[0]._dummy_hash)
            assert params.type is _password_hasher.Type.ID
            assert (params.memory_cost, params.time_cost, params.parallelism) == (65536, 3, 1)
            assert (params.hash_len, params.salt_len, params.version) == (32, 16, 19)
            # User hashing never enters the artifact and retains fresh salts.
            hashes = [service.hash_password("independent-user-password") for service in services]
            assert len(set(hashes)) == 4
            assert counts["hash"] == 5
            assert all(service.verify_password(phc, "independent-user-password")
                       for service, phc in zip(services, hashes, strict=True))
            assert counts["verify"] == 12
            assert all(phc != services[0]._dummy_hash for phc in hashes)
        finally:
            sys.setprofile(previous)

    @pytest.mark.parametrize("change", [
        {"version": 2}, {"memory_cost": 65537}, {"time_cost": 4},
        {"parallelism": 2}, {"hash_len": 33}, {"salt_len": 17},
    ])
    def test_complete_policy_key_and_single_entry_bound(self, change) -> None:
        import dataclasses

        from argon2 import extract_parameters

        from shared.identity import credential_service as credentials

        first = CredentialService()
        policy = dataclasses.replace(CURRENT_POLICY, **change)
        other = CredentialService(policy)
        again = CredentialService(policy)
        assert other._dummy_hash == again._dummy_hash
        assert other._dummy_hash != first._dummy_hash
        params = extract_parameters(other._dummy_hash)
        for field in ("memory_cost", "time_cost", "parallelism", "hash_len", "salt_len"):
            assert getattr(params, field) == getattr(policy, field)
        other.dummy_verify()
        assert credentials._dummy_artifact == (policy, other._dummy_hash)
        returned = CredentialService()
        assert returned._dummy_hash != first._dummy_hash  # eviction, not unbounded per-policy state
        assert credentials._dummy_artifact == (CURRENT_POLICY, returned._dummy_hash)

    def test_concurrent_real_initialization_is_single_and_clients_are_isolated(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        barrier = Barrier(4)

        def construct(_):
            barrier.wait(timeout=20)
            return CredentialService()

        with ThreadPoolExecutor(max_workers=4) as pool:
            services = list(pool.map(construct, range(4)))
        assert len({service._dummy_hash for service in services}) == 1
        assert len({id(service._hasher) for service in services}) == 4
        for service in services:
            service.dummy_verify()
        # Instance mutations do not alter another service or retained PHC.
        services[0]._hasher.encoding = "ascii"
        assert services[1]._hasher.encoding == "utf-8"
        assert CredentialService()._hasher.encoding == "utf-8"

    def test_unavailable_backend_does_not_use_warm_artifact(self, monkeypatch) -> None:
        from shared.identity import credential_service as credentials

        CredentialService()
        monkeypatch.setattr(credentials, "_ARGON2_AVAILABLE", False)
        with pytest.raises(RuntimeError, match="argon2-cffi is required"):
            CredentialService()

    def test_builder_failure_does_not_use_warm_artifact(self, monkeypatch) -> None:
        CredentialService()

        def fail(policy):
            raise RuntimeError("initialization refused")

        monkeypatch.setattr(CredentialService, "_build_hasher", staticmethod(fail))
        with pytest.raises(RuntimeError, match="initialization refused"):
            CredentialService()

    def test_changed_real_backend_is_not_reused_or_published(self, monkeypatch) -> None:
        from argon2 import _password_hasher

        from shared.identity import credential_service as credentials

        first = CredentialService()
        real_hash = _password_hasher.hash_secret
        calls = []

        def observing_hash(**kwargs):
            calls.append(kwargs)
            return real_hash(**kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(_password_hasher, "hash_secret", observing_hash)
            second, third = CredentialService(), CredentialService()
            second.dummy_verify()
            assert len(calls) == 2
            assert second._dummy_hash != third._dummy_hash
            assert credentials._dummy_artifact is None
            assert all((call["memory_cost"], call["time_cost"], call["parallelism"])
                       == (65536, 3, 1) for call in calls)
        assert CredentialService()._dummy_hash != first._dummy_hash

    def test_hash_failure_invalidates_warm_artifact_and_retry_is_fresh(self, monkeypatch) -> None:
        from argon2 import _password_hasher
        from argon2.exceptions import HashingError

        from shared.identity import credential_service as credentials

        first = CredentialService()

        def fail(**kwargs):
            raise HashingError("backend refused")

        with monkeypatch.context() as patch:
            patch.setattr(_password_hasher, "hash_secret", fail)
            with pytest.raises(HashingError, match="backend refused"):
                CredentialService()
            assert credentials._dummy_artifact is None
        recovered = CredentialService()
        assert recovered._dummy_hash != first._dummy_hash
        recovered.dummy_verify()

    def test_every_dummy_call_propagates_backend_verification_failure(self, monkeypatch) -> None:
        from argon2 import _password_hasher
        from argon2.exceptions import VerificationError

        service = CredentialService()
        service.dummy_verify()
        attempts = []

        def fail(*args):
            attempts.append(args)
            raise VerificationError("verification backend unavailable")

        monkeypatch.setattr(_password_hasher, "verify_secret", fail)
        for _ in range(2):
            with pytest.raises(VerificationError, match="verification backend unavailable"):
                service.dummy_verify()
        assert len(attempts) == 2

    def test_real_hasher_subclass_is_not_shared(self, monkeypatch) -> None:
        from argon2 import PasswordHasher

        from shared.identity import credential_service as credentials

        CredentialService()

        class AlternateHasher(PasswordHasher):
            pass

        def build(policy):
            return AlternateHasher(
                memory_cost=policy.memory_cost, time_cost=policy.time_cost,
                parallelism=policy.parallelism, hash_len=policy.hash_len,
                salt_len=policy.salt_len,
            )

        monkeypatch.setattr(CredentialService, "_build_hasher", staticmethod(build))
        first, second = CredentialService(), CredentialService()
        assert first._dummy_hash != second._dummy_hash
        assert credentials._dummy_artifact is None
        first.dummy_verify()
        second.dummy_verify()

    def test_invalid_policy_cannot_use_warm_artifact(self) -> None:
        import dataclasses

        from argon2.exceptions import HashingError

        CredentialService()
        with pytest.raises(HashingError):
            CredentialService(dataclasses.replace(CURRENT_POLICY, memory_cost=0))

    @pytest.mark.parametrize("phc", ["not-a-phc", "$argon2id$v=19$m=32768,t=2,p=1$bad$bad"])
    def test_incompatible_artifact_fails_closed_and_is_evicted(self, monkeypatch, phc) -> None:
        from shared.identity import credential_service as credentials

        CredentialService()
        monkeypatch.setattr(credentials, "_dummy_artifact", (CURRENT_POLICY, phc))
        with pytest.raises((ValueError, RuntimeError)):
            CredentialService()
        assert credentials._dummy_artifact is None
        CredentialService().dummy_verify()

    def test_custom_builder_retains_fresh_real_initialization(self, monkeypatch) -> None:
        from shared.identity import credential_service as credentials

        first = CredentialService()
        stock = CredentialService._build_hasher

        def build(policy):
            return stock(policy)

        monkeypatch.setattr(CredentialService, "_build_hasher", staticmethod(build))
        second, third = CredentialService(), CredentialService()
        assert len({first._dummy_hash, second._dummy_hash, third._dummy_hash}) == 3
        assert credentials._dummy_artifact is None
        second.dummy_verify()
        third.dummy_verify()

    def test_fork_child_resets_inherited_locked_state(self) -> None:
        import os

        from shared.identity import credential_service as credentials

        parent = CredentialService()
        read_fd, write_fd = os.pipe()
        # Deliberately fork while locked: the child must not inherit deadlock.
        with credentials._dummy_lock:
            pid = os.fork()
        if pid == 0:
            try:
                import signal

                signal.alarm(20)
                os.close(read_fd)
                assert credentials._dummy_artifact is None
                child = CredentialService()
                child.dummy_verify()
                assert child._dummy_hash != parent._dummy_hash
                os.write(write_fd, b"real-child-ok")
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(write_fd)
        try:
            import select

            assert select.select([read_fd], [], [], 20)[0], "fork child did not complete"
            assert os.read(read_fd, 100) == b"real-child-ok"
        finally:
            os.close(read_fd)
            waited, status = os.waitpid(pid, 0)
        assert waited == pid and os.waitstatus_to_exitcode(status) == 0
        assert credentials._dummy_artifact == (CURRENT_POLICY, parent._dummy_hash)
