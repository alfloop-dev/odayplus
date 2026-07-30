import { describe, expect, it } from "vitest";

describe("Vitest network regression guard", () => {
  it("fails on unexpected unmocked network request to localhost", async () => {
    await expect(fetch("http://127.0.0.1:3000/api/unexpected-endpoint")).rejects.toThrow(
      "Unexpected unmocked network request",
    );
  });
});
