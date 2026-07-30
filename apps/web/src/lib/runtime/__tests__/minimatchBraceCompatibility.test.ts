import { describe, expect, it } from "vitest";
// @ts-expect-error minimatch type declarations not present in web workspace
import minimatch from "minimatch";

describe("minimatch brace expansion compatibility", () => {
  it("expands brace patterns without throwing TypeError: expand is not a function", () => {
    expect(minimatch("a", "{a,b}")).toBe(true);
    expect(minimatch("c", "{a,b}")).toBe(false);
  });
});
