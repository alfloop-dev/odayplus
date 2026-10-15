import { constantTimeEqual } from "./crypto";
import { getDefaultIdentityStore, type IdentityStore } from "./identityStore";
import { isProductionWebRuntime } from "./runtime";

export {
  LOCAL_IDENTITY_ISSUER,
  LOCAL_IDENTITY_KEY_ID,
  localJwtExpiresAt,
  localJwtNeedsRefresh,
  mintLocalJwt,
} from "./localJwt";

const encoder = new TextEncoder();

export const COMMON_WEAK_PASSWORDS = new Set([
  "password123456",
  "123456789012",
  "1234567890123",
  "admin12345678",
  "adminadmin123",
  "welcome123456",
  "qwertyuiop12",
  "letmein123456",
  "iloveyou123456",
  "changeme123456",
  "odayplus123456",
  "passwordpassword",
  "defaultpassword123",
  "testpassword123",
]);

export type PasswordPolicyResult =
  | { valid: true }
  | { valid: false; code: "AUTH_PASSWORD_POLICY_VIOLATION"; reason: string };

export function validatePasswordPolicy(
  password: string,
  username?: string,
  email?: string,
  currentPassword?: string,
): PasswordPolicyResult {
  const normalizedPassword = (password || "").normalize("NFKC");

  if (currentPassword !== undefined) {
    const normalizedCurrent = (currentPassword || "").normalize("NFKC");
    if (normalizedCurrent && normalizedCurrent === normalizedPassword) {
      return {
        valid: false,
        code: "AUTH_PASSWORD_POLICY_VIOLATION",
        reason: "New password must be different from current password.",
      };
    }
  }

  if (normalizedPassword.length < 12) {
    return {
      valid: false,
      code: "AUTH_PASSWORD_POLICY_VIOLATION",
      reason: "Password must be at least 12 characters long.",
    };
  }

  if (normalizedPassword.length > 1024) {
    return {
      valid: false,
      code: "AUTH_PASSWORD_POLICY_VIOLATION",
      reason: "Password must not exceed 1024 characters.",
    };
  }

  const lowerPassword = normalizedPassword.toLowerCase();

  if (COMMON_WEAK_PASSWORDS.has(lowerPassword)) {
    return {
      valid: false,
      code: "AUTH_PASSWORD_POLICY_VIOLATION",
      reason: "Password is too common or easily guessable.",
    };
  }

  if (username) {
    const normalizedUsername = username.normalize("NFKC").toLowerCase().trim();
    if (
      normalizedUsername.length >= 3 &&
      (lowerPassword === normalizedUsername ||
        lowerPassword.includes(normalizedUsername))
    ) {
      return {
        valid: false,
        code: "AUTH_PASSWORD_POLICY_VIOLATION",
        reason: "Password cannot contain or equal the username.",
      };
    }
  }

  if (email) {
    const normalizedEmail = email.normalize("NFKC").toLowerCase().trim();
    const emailPrefix = normalizedEmail.split("@")[0];
    if (
      (normalizedEmail && lowerPassword === normalizedEmail) ||
      (emailPrefix &&
        emailPrefix.length >= 3 &&
        lowerPassword.includes(emailPrefix))
    ) {
      return {
        valid: false,
        code: "AUTH_PASSWORD_POLICY_VIOLATION",
        reason: "Password cannot contain or equal the email address.",
      };
    }
  }

  return { valid: true };
}

export type LocalAuthResult =
  | {
      ok: true;
      account: {
        id: string;
        username: string;
        email?: string;
        tenantId: string;
      };
      mustChangePassword?: boolean;
      /** Server-only revision of the credential actually verified. */
      verifiedCredentialRevision?: string;
    }
  | {
      ok: false;
      code: "AUTH_INVALID_CREDENTIALS" | "AUTH_ACCOUNT_LOCKED";
      summary: string;
    };

export async function dummyTimingEqualization(): Promise<void> {
  // Constant-time dummy work to equalize timing and prevent account enumeration.
  const dummyInput = encoder.encode("dummy-account-timing-equalization");
  await crypto.subtle.digest("SHA-256", dummyInput);
}

export async function authenticateLocalCredentials(
  usernameInput?: string | null,
  passwordInput?: string | null,
  options: {
    environment?: Record<string, string | undefined>;
    fetchImpl?: typeof fetch;
    identityStore?: IdentityStore | null;
    mockAccounts?: Map<
      string,
      {
        id: string;
        username: string;
        password: string;
        tenantId: string;
        locked?: boolean;
      }
    >;
  } = {},
): Promise<LocalAuthResult> {
  const username = (usernameInput || "").normalize("NFKC").trim();
  const password = (passwordInput || "").normalize("NFKC");

  if (!username || !password) {
    await dummyTimingEqualization();
    return {
      ok: false,
      code: "AUTH_INVALID_CREDENTIALS",
      summary: "Invalid username or password.",
    };
  }

  // Check if mock accounts were provided (e.g. for unit testing or test environments)
  if (options.mockAccounts) {
    const candidate = options.mockAccounts.get(username.toLowerCase());
    if (!candidate) {
      await dummyTimingEqualization();
      return {
        ok: false,
        code: "AUTH_INVALID_CREDENTIALS",
        summary: "Invalid username or password.",
      };
    }

    if (!constantTimeEqual(candidate.password, password)) {
      await dummyTimingEqualization();
      return {
        ok: false,
        code: "AUTH_INVALID_CREDENTIALS",
        summary: "Invalid username or password.",
      };
    }

    // Do not reveal a lock before the password has been proven correct.
    if (candidate.locked) {
      return {
        ok: false,
        code: "AUTH_ACCOUNT_LOCKED",
        summary: "Account is temporarily locked.",
      };
    }

    return {
      ok: true,
      account: {
        id: candidate.id,
        username: candidate.username,
        tenantId: candidate.tenantId,
      },
    };
  }

  // Try the identity store (production path)
  const store =
    options.identityStore !== undefined
      ? options.identityStore
      : getDefaultIdentityStore();

  if (store) {
    try {
      const account = await store.findAccountByUsername(username);
      if (!account) {
        await store.dummyVerify();
        return {
          ok: false,
          code: "AUTH_INVALID_CREDENTIALS",
          summary: "Invalid username or password.",
        };
      }

      const credential = await store.getPasswordCredential(account.accountId);
      if (!credential) {
        await store.dummyVerify();
        return {
          ok: false,
          code: "AUTH_INVALID_CREDENTIALS",
          summary: "Invalid username or password.",
        };
      }

      // Verify the password before revealing that the account is locked. This
      // keeps AUTH_ACCOUNT_LOCKED from becoming an account-enumeration oracle.
      const { valid, newHash } = await store.verifyPassword(
        credential.phcHash,
        password,
      );
      if (!valid) {
        return {
          ok: false,
          code: "AUTH_INVALID_CREDENTIALS",
          summary: "Invalid username or password.",
        };
      }

      if (account.status === "locked") {
        return {
          ok: false,
          code: "AUTH_ACCOUNT_LOCKED",
          summary: "Account is temporarily locked.",
        };
      }
      if (account.status === "disabled" || account.status === "invited") {
        return {
          ok: false,
          code: "AUTH_INVALID_CREDENTIALS",
          summary: "Invalid username or password.",
        };
      }

      // Carry the revision read with this PHC through to session insertion.
      // Never read a newer revision and attach it to an older verification.
      let verifiedCredentialRevision = credential.credentialRevision;
      if (newHash) {
        try {
          const committedRevision = await store.updatePasswordHash(
            account.accountId, newHash, verifiedCredentialRevision,
          );
          if (typeof committedRevision === "string") verifiedCredentialRevision = committedRevision;
        } catch {
          // Rehash availability remains non-fatal, but retain the OLD revision:
          // a concurrent rotation then fails the DB session-insert fence.
        }
      }

      return {
        ok: true,
        account: {
          id: account.accountId,
          username: account.username,
          email: account.email,
          tenantId: account.tenantId,
        },
        mustChangePassword: credential.mustChange,
        verifiedCredentialRevision,
      };
    } catch {
      // Database outages and malformed credentials fail closed with the same
      // public response as an unknown account.
      return {
        ok: false,
        code: "AUTH_INVALID_CREDENTIALS",
        summary: "Invalid username or password.",
      };
    }
  }

  // Dev fallback: non-production without DB
  const env = options.environment ?? process.env;
  if (!isProductionWebRuntime(env)) {
    // In development / testing mode, support standard test users if no external store
    if (
      (username === "admin" && password === "Admin12345678!") ||
      (username === "operator" && password === "Operator123456!") ||
      (username === "user-123" && password === "ValidPassword123!")
    ) {
      return {
        ok: true,
        account: {
          id: `acc-${username}`,
          username,
          email: `${username}@example.invalid`,
          tenantId: "default",
        },
      };
    }
  }

  await dummyTimingEqualization();
  return {
    ok: false,
    code: "AUTH_INVALID_CREDENTIALS",
    summary: "Invalid username or password.",
  };
}
