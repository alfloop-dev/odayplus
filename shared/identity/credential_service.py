"""Argon2id 密碼憑證服務。

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §6.1
- 演算法固定 Argon2id，不得使用其他雜湊
- 參數：memory_cost ≥ 65536 KiB, time_cost ≥ 3, parallelism = 1
- 儲存格式：PHC 編碼字串
- 可升級性：rehash-on-verify，不要求使用者重設密碼
- Dummy verify 防止帳號枚舉時序攻擊
"""
from __future__ import annotations

import dataclasses
import os
import threading
from typing import Any

try:
    from argon2 import PasswordHasher, Type, _password_hasher, low_level  # type: ignore[import-untyped]
    from argon2.exceptions import VerifyMismatchError  # type: ignore[import-untyped]

    _ARGON2_AVAILABLE = True
except ImportError:  # pragma: no cover — CI may not have argon2-cffi
    _ARGON2_AVAILABLE = False


# ────────────────────────────────────────────────────────────────────────────
# Argon2id 參數政策（Contract §6.1）
# ────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Argon2Policy:
    """集中管理的 Argon2id 參數集，帶版本號以支援升級判斷。"""

    version: int = 1
    memory_cost: int = 65536  # KiB (64 MiB)
    time_cost: int = 3        # iterations
    parallelism: int = 1      # Cloud Run 單請求可預測性
    hash_len: int = 32        # output bytes
    salt_len: int = 16        # minimum CSPRNG salt bytes

    def to_params_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.version,
            "memory_cost": self.memory_cost,
            "time_cost": self.time_cost,
            "parallelism": self.parallelism,
            "hash_len": self.hash_len,
            "salt_len": self.salt_len,
        }


# 目前生效的政策
CURRENT_POLICY = Argon2Policy()


# ────────────────────────────────────────────────────────────────────────────
# 密碼憑證服務
# ────────────────────────────────────────────────────────────────────────────

class CredentialService:
    """Argon2id 密碼雜湊與驗證服務。

    所有公開方法皆為同步（Argon2 CPU-bound），呼叫端應在 thread pool
    中執行以避免阻塞事件迴圈。
    """

    def __init__(self, policy: Argon2Policy | None = None) -> None:
        if not _ARGON2_AVAILABLE:
            raise RuntimeError(
                "argon2-cffi is required for CredentialService. "
                "Install with: pip install argon2-cffi"
            )
        self._policy = policy or CURRENT_POLICY
        self._hasher = self._build_hasher(self._policy)
        # Eager on BOTH account paths; only the non-secret initialization PHC
        # may be reused. Every service still builds/validates its own hasher and
        # every dummy_verify below still performs the real full-cost operation.
        self._dummy_hash = _timing_dummy(self)

    @property
    def policy(self) -> Argon2Policy:
        return self._policy

    # ── 雜湊 ──────────────────────────────────────────────────────────────

    def hash_password(self, password: str) -> str:
        """以目前政策對密碼做 Argon2id 雜湊，回傳 PHC 編碼字串。"""
        return self._hasher.hash(password)

    # ── 驗證 ──────────────────────────────────────────────────────────────

    def verify_password(self, phc_hash: str, password: str) -> bool:
        """驗證密碼是否符合 PHC 雜湊。

        Returns:
            True — 密碼正確
        Raises:
            VerifyMismatchError — 密碼錯誤
            InvalidHashError — PHC 格式無效
        """
        return self._hasher.verify(phc_hash, password)

    def needs_rehash(self, phc_hash: str) -> bool:
        """檢查 PHC 雜湊的參數是否低於目前政策。

        Contract §6.1: 登入驗證成功後，若儲存的參數低於當前政策，
        必須以當次明文密碼重新雜湊並寫回。
        """
        return self._hasher.check_needs_rehash(phc_hash)

    def verify_and_rehash(
        self, phc_hash: str, password: str
    ) -> tuple[bool, str | None]:
        """驗證密碼，若通過且需要 rehash 則回傳新雜湊。

        Returns:
            (True, new_phc_hash) — 密碼正確且已 rehash
            (True, None) — 密碼正確且不需 rehash
        Raises:
            VerifyMismatchError — 密碼錯誤
        """
        self._hasher.verify(phc_hash, password)
        if self._hasher.check_needs_rehash(phc_hash):
            new_hash = self._hasher.hash(password)
            return True, new_hash
        return True, None

    # ── Dummy verify（防時序攻擊） ────────────────────────────────────────

    def dummy_verify(self) -> None:
        """執行等價成本的 Argon2id 驗證，防止帳號枚舉（§6.3）。

        帳號不存在時必須呼叫此方法，使回應時序與密碼錯誤不可區分。
        """
        try:
            self._hasher.verify(self._dummy_hash, "wrong-password-for-dummy")
        except VerifyMismatchError:
            pass  # 預期結果

    # ── 內部 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _build_hasher(policy: Argon2Policy) -> PasswordHasher:
        return PasswordHasher(
            time_cost=policy.time_cost,
            memory_cost=policy.memory_cost,
            parallelism=policy.parallelism,
            hash_len=policy.hash_len,
            salt_len=policy.salt_len,
            type=Type.ID,  # Argon2id
        )

    @staticmethod
    def extract_params_from_phc(phc_hash: str) -> dict[str, Any]:
        """從 PHC 字串萃取參數，供稽核與升級判斷使用。

        PHC 格式: $argon2id$v=19$m=65536,t=3,p=1$<salt>$<hash>
        """
        parts = phc_hash.split("$")
        if len(parts) < 4 or parts[1] != "argon2id":
            raise ValueError(f"Not a valid Argon2id PHC hash: {phc_hash[:20]}...")

        params: dict[str, Any] = {"algorithm": "argon2id"}
        # parts[3] = "m=65536,t=3,p=1"
        for kv in parts[3].split(","):
            key, _, val = kv.partition("=")
            if key == "m":
                params["memory_cost"] = int(val)
            elif key == "t":
                params["time_cost"] = int(val)
            elif key == "p":
                params["parallelism"] = int(val)
        return params


# A single process-local immutable initialization artifact, never a credential,
# hasher/client, password, verification result or authorization/session cache.
# Unknown/custom backends/builders deliberately retain eager fresh hashing.
_DUMMY_PASSWORD = "dummy-password-for-timing-safety"
_dummy_lock = threading.Lock()
_dummy_artifact: tuple[Argon2Policy, str] | None = None
_stock_builder = CredentialService._build_hasher


def _backend_signature() -> tuple[Any, ...]:
    return (
        PasswordHasher, PasswordHasher.__init__, PasswordHasher.hash,
        PasswordHasher.verify, PasswordHasher.check_needs_rehash,
        _password_hasher.hash_secret, _password_hasher.verify_secret,
        _password_hasher.extract_parameters, _password_hasher.validate_params_for_platform,
        low_level.hash_secret, low_level.verify_secret, low_level.ffi, low_level.lib,
        low_level.ARGON2_VERSION, Type, os.urandom,
    )


_stock_backend = _backend_signature() if _ARGON2_AVAILABLE else None


def _reset_timing_dummy() -> None:
    # A fork may inherit a lock held by a vanished thread. Never acquire it in
    # the child, and never reuse the parent's initialization artifact there.
    global _dummy_lock, _dummy_artifact
    _dummy_lock = threading.Lock()
    _dummy_artifact = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_timing_dummy)


def _timing_dummy(service: CredentialService) -> str:
    global _dummy_artifact
    hasher, policy = service._hasher, service.policy
    with _dummy_lock:
        compatible = (
            type(service) is CredentialService
            and type(policy) is Argon2Policy
            and all(type(value) is int for value in dataclasses.astuple(policy))
            and service._build_hasher is _stock_builder
            and _backend_signature() == _stock_backend
            and type(hasher) is PasswordHasher
        )
        if not compatible:
            # Changed/failed/test backends cannot consume or populate stock
            # state. Invalidate even if their fresh initialization later fails.
            _dummy_artifact = None
            return hasher.hash(_DUMMY_PASSWORD)
        if _dummy_artifact is not None and _dummy_artifact[0] == policy:
            phc = _dummy_artifact[1]
            try:
                if hasher.check_needs_rehash(phc):
                    raise RuntimeError("Incompatible Argon2 timing initialization")
            except Exception:
                _dummy_artifact = None
                raise
            return phc
        # Evict BEFORE initialization: a failure cannot publish a partial value
        # or leave a prior policy's artifact usable. At most one PHC is retained.
        _dummy_artifact = None
        phc = hasher.hash(_DUMMY_PASSWORD)
        if hasher.check_needs_rehash(phc):
            raise RuntimeError("Incompatible Argon2 timing initialization")
        _dummy_artifact = (policy, phc)
        return phc
