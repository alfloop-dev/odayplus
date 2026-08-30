"""密碼政策檢查。

Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §6.2
- 長度 ≥ 12 且 ≤ 1024 字元
- 輸入先做 Unicode NFKC 正規化
- 不得強制組成規則（大小寫/符號/數字）（NIST 800-63B）
- 必須比對弱密碼拒絕清單
- 拒絕與 username / email 高度相同者
- 不得設定密碼提示問答、密碼歷史明文保存或密碼寄送
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

# ────────────────────────────────────────────────────────────────────────────
# 弱密碼拒絕清單（離線清單，可在部署時擴充）
# ────────────────────────────────────────────────────────────────────────────

# 常見弱密碼的最小集合。生產環境應載入完整清單（例如 HIBP top-10k）。
_DEFAULT_WEAK_PASSWORDS: frozenset[str] = frozenset({
    "password", "123456789012", "000000000000", "qwertyuiop12",
    "iloveyou1234", "admin1234567", "welcome12345", "password1234",
    "letmein12345", "trustno1pass", "changeme1234", "master123456",
    "dragon123456", "monkey123456", "shadow123456", "sunshine1234",
    "princess1234", "football1234", "charlie12345", "superman1234",
    "qwerty123456", "michael12345", "ashley123456", "bailey123456",
    "passw0rd1234", "1234567890ab", "abcdefghijkl", "abcdef123456",
    "aaaaaaaaaaaa", "111111111111", "password12345", "p@ssword1234",
})


# ────────────────────────────────────────────────────────────────────────────
# 政策錯誤碼
# ────────────────────────────────────────────────────────────────────────────

class PasswordPolicyError:
    """密碼政策違規的統一錯誤碼。"""

    TOO_SHORT = "PASSWORD_TOO_SHORT"
    TOO_LONG = "PASSWORD_TOO_LONG"
    IN_WEAK_LIST = "PASSWORD_IN_WEAK_LIST"
    SIMILAR_TO_USERNAME = "PASSWORD_SIMILAR_TO_USERNAME"
    SIMILAR_TO_EMAIL = "PASSWORD_SIMILAR_TO_EMAIL"


@dataclass(frozen=True)
class PolicyViolation:
    """單一政策違規。"""
    code: str
    message: str


@dataclass(frozen=True)
class PasswordPolicyResult:
    """密碼政策檢查結果。"""
    valid: bool
    violations: tuple[PolicyViolation, ...] = ()

    @staticmethod
    def ok() -> PasswordPolicyResult:
        return PasswordPolicyResult(valid=True)

    @staticmethod
    def fail(*violations: PolicyViolation) -> PasswordPolicyResult:
        return PasswordPolicyResult(valid=False, violations=violations)


# ────────────────────────────────────────────────────────────────────────────
# 密碼政策服務
# ────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PasswordPolicyConfig:
    """密碼政策可調參數。"""
    min_length: int = 12
    max_length: int = 1024
    similarity_threshold: float = 0.7  # Jaccard 字元 bigram 相似度門檻
    weak_passwords: frozenset[str] = field(default_factory=lambda: _DEFAULT_WEAK_PASSWORDS)


class PasswordPolicy:
    """密碼政策檢查器（Contract §6.2）。

    所有檢查都在 NFKC 正規化後的密碼上執行。
    """

    def __init__(self, config: PasswordPolicyConfig | None = None) -> None:
        self._config = config or PasswordPolicyConfig()

    @property
    def config(self) -> PasswordPolicyConfig:
        return self._config

    def validate(
        self,
        password: str,
        *,
        username: str | None = None,
        email: str | None = None,
    ) -> PasswordPolicyResult:
        """驗證密碼是否符合政策。

        Args:
            password: 原始密碼（自動做 NFKC 正規化）
            username: 比對相似度用（可選）
            email: 比對相似度用（可選）

        Returns:
            PasswordPolicyResult 包含所有違規項目
        """
        normalized = self.normalize(password)
        violations: list[PolicyViolation] = []

        # 長度檢查
        if len(normalized) < self._config.min_length:
            violations.append(PolicyViolation(
                code=PasswordPolicyError.TOO_SHORT,
                message=f"密碼長度不得少於 {self._config.min_length} 字元",
            ))
        if len(normalized) > self._config.max_length:
            violations.append(PolicyViolation(
                code=PasswordPolicyError.TOO_LONG,
                message=f"密碼長度不得超過 {self._config.max_length} 字元",
            ))

        # 弱密碼清單比對（NFKC + lower）
        if normalized.lower() in self._config.weak_passwords:
            violations.append(PolicyViolation(
                code=PasswordPolicyError.IN_WEAK_LIST,
                message="此密碼過於常見，請選擇更安全的密碼",
            ))

        # 與 username 相似度檢查
        if username and self._is_similar(normalized, username):
            violations.append(PolicyViolation(
                code=PasswordPolicyError.SIMILAR_TO_USERNAME,
                message="密碼不得與使用者名稱高度相似",
            ))

        # 與 email local part 相似度檢查
        if email:
            local_part = email.split("@")[0] if "@" in email else email
            if self._is_similar(normalized, local_part):
                violations.append(PolicyViolation(
                    code=PasswordPolicyError.SIMILAR_TO_EMAIL,
                    message="密碼不得與電子郵件地址高度相似",
                ))

        if violations:
            return PasswordPolicyResult.fail(*violations)
        return PasswordPolicyResult.ok()

    @staticmethod
    def normalize(password: str) -> str:
        """Unicode NFKC 正規化（§6.2）。"""
        return unicodedata.normalize("NFKC", password)

    def _is_similar(self, password: str, reference: str) -> bool:
        """以字元 bigram Jaccard 相似度判斷是否「高度相同」。"""
        if not reference or len(reference) < 3:
            return False

        pw_lower = password.lower()
        ref_lower = reference.lower()

        # 完全包含視為相似
        if ref_lower in pw_lower or pw_lower in ref_lower:
            return True

        # Jaccard bigram similarity
        pw_bigrams = set(_bigrams(pw_lower))
        ref_bigrams = set(_bigrams(ref_lower))

        if not pw_bigrams or not ref_bigrams:
            return False

        intersection = len(pw_bigrams & ref_bigrams)
        union = len(pw_bigrams | ref_bigrams)
        similarity = intersection / union if union > 0 else 0.0

        return similarity >= self._config.similarity_threshold


def _bigrams(text: str) -> list[str]:
    """產生字元 bigram 清單。"""
    return [text[i:i + 2] for i in range(len(text) - 1)]
