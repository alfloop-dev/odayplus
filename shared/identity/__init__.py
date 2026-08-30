"""Identity services — Argon2id 密碼憑證、密碼政策與 session 管理。

Task: ODP-WEB-LOCAL-IDENTITY-CORE-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §5, §6, §7

本模組實作 invite-only 本地帳號的核心服務層：
- CredentialService: Argon2id 雜湊、驗證、rehash-on-verify、dummy verify
- PasswordPolicy: 長度、NFKC、弱密碼比對、近似 username/email 檢查
- SessionService: 建立、輪替、撤銷、idle/absolute 到期
- LoginThrottleService: 帳號與 IP 維度的節流與鎖定
"""
