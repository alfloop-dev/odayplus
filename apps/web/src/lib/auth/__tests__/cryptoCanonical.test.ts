import { describe, expect, it } from "vitest";
import { base64UrlDecode, openJson, sealJson } from "../crypto";

const SECRET = "OFFLINE-canonical-envelope-key-at-least-32-bytes";
describe("one canonical encrypted envelope spelling", () => {
  it("refuses explicit padding and nonzero trailing pad bits although decoded ciphertext bytes are equal", async () => {
    const token = await sealJson({ value: "x" }, "offline-canonical", SECRET);
    const [version, iv, cipher] = token.split(".");
    expect(cipher.length % 4).toBe(3);
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    const index = alphabet.indexOf(cipher.at(-1)!);
    const bits = cipher.slice(0, -1) + alphabet[index | 1];
    for (const value of [cipher + "=", bits]) {
      expect(Array.from(base64UrlDecode(value))).toEqual(Array.from(base64UrlDecode(cipher)));
      expect(await openJson(`${version}.${iv}.${value}`, "offline-canonical", SECRET)).toBeNull();
    }
    expect(await openJson(token, "offline-canonical", SECRET)).toEqual({ value: "x" });
  });
});
