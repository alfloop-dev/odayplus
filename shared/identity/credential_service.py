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
from typing import Any

try:
    from argon2 import PasswordHasher, Type  # type: ignore[import-untyped]
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
        # 預先計算 dummy hash，用於帳號不存在時的等價成本驗證（§6.3）
        self._dummy_hash = self._hasher.hash("dummy-password-for-timing-safety")

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
