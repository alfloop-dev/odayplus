"""Identity services — Argon2id 密碼憑證、密碼政策、session 管理與權威身份庫。

Task: ODP-WEB-LOCAL-IDENTITY-CORE-001, ODP-WEB-LOCAL-AUTH-API-TRUST-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2, §3, §4, §5, §6, §7

本模組實作 invite-only 本地帳號的核心服務層與權威身份庫：
- Account, FederatedIdentity, IdentityStore, InMemoryIdentityStore, SqlIdentityStore
- CredentialService: Argon2id 雜湊、驗證、rehash-on-verify、dummy verify
- PasswordPolicy: 長度、NFKC、弱密碼比對、近似 username/email 檢查
- Session, SessionService, SessionRepository, SessionConfig, RevocationReason
- SqlSessionRepository: identity.sessions 的持久實作（撤銷跨 process 生效）
- LoginThrottleService: 帳號與 IP 維度的節流與鎖定
"""

from .credential_service import CURRENT_POLICY, Argon2Policy, CredentialService
from .login_throttle import (
    LoginAttemptRecord,
    LoginThrottleService,
    ThrottleRepository,
    account_attempt_key,
    ip_attempt_key,
)
from .password_policy import (
    PasswordPolicy,
    PasswordPolicyError,
    PasswordPolicyResult,
    PolicyViolation,
)
from .session_service import (
    InMemorySessionRepository,
    RevocationReason,
    Session,
    SessionConfig,
    SessionRepository,
    SessionService,
)
from .session_store import SqlSessionRepository
from .store import (
    Account,
    FederatedIdentity,
    IdentityStore,
    InMemoryIdentityStore,
    SqlIdentityStore,
)

__all__ = [
    "Account",
    "Argon2Policy",
    "CURRENT_POLICY",
    "CredentialService",
    "FederatedIdentity",
    "IdentityStore",
    "InMemoryIdentityStore",
    "InMemorySessionRepository",
    "LoginAttemptRecord",
    "LoginThrottleService",
    "PasswordPolicy",
    "PasswordPolicyError",
    "PasswordPolicyResult",
    "PolicyViolation",
    "RevocationReason",
    "Session",
    "SessionConfig",
    "SessionRepository",
    "SessionService",
    "SqlIdentityStore",
    "SqlSessionRepository",
    "ThrottleRepository",
    "account_attempt_key",
    "ip_attempt_key",
]
