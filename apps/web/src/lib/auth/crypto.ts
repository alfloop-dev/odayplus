const encoder = new TextEncoder();
const decoder = new TextDecoder();
const KEY_SALT = encoder.encode("oday-plus-web-auth-v1");

export function base64UrlEncode(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

export function base64UrlDecode(value: string): Uint8Array {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(
    normalized.length + ((4 - (normalized.length % 4)) % 4),
    "=",
  );
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function sessionSecret(explicitSecret?: string): string {
  const secret = explicitSecret ?? process.env.ODP_WEB_SESSION_SECRET;
  if (!secret || encoder.encode(secret).byteLength < 32) {
    throw new Error("ODP_WEB_SESSION_SECRET must contain at least 32 bytes");
  }
  return secret;
}

async function encryptionKey(
  purpose: string,
  explicitSecret?: string,
): Promise<CryptoKey> {
  const baseKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(sessionSecret(explicitSecret)),
    "HKDF",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: KEY_SALT,
      info: encoder.encode(purpose),
    },
    baseKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

export async function sealJson(
  payload: unknown,
  purpose: string,
  explicitSecret?: string,
): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintext = encoder.encode(JSON.stringify(payload));
  const ciphertext = await crypto.subtle.encrypt(
    {
      name: "AES-GCM",
      iv,
      additionalData: encoder.encode(purpose),
      tagLength: 128,
    },
    await encryptionKey(purpose, explicitSecret),
    plaintext,
  );
  return `v1.${base64UrlEncode(iv)}.${base64UrlEncode(
    new Uint8Array(ciphertext),
  )}`;
}

export async function openJson<T>(
  token: string | null | undefined,
  purpose: string,
  explicitSecret?: string,
): Promise<T | null> {
  if (!token) return null;
  const [version, ivValue, ciphertextValue, ...rest] = token.split(".");
  if (version !== "v1" || !ivValue || !ciphertextValue || rest.length) {
    return null;
  }

  try {
    const plaintext = await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: base64UrlDecode(ivValue),
        additionalData: encoder.encode(purpose),
        tagLength: 128,
      },
      await encryptionKey(purpose, explicitSecret),
      base64UrlDecode(ciphertextValue),
    );
    return JSON.parse(decoder.decode(plaintext)) as T;
  } catch {
    return null;
  }
}

export function randomBase64Url(byteLength = 32): string {
  return base64UrlEncode(crypto.getRandomValues(new Uint8Array(byteLength)));
}

export async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return base64UrlEncode(new Uint8Array(digest));
}

export function constantTimeEqual(left: string, right: string): boolean {
  const length = Math.max(left.length, right.length);
  let mismatch = left.length ^ right.length;
  for (let index = 0; index < length; index += 1) {
    mismatch |=
      (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return mismatch === 0;
}

