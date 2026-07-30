import "@testing-library/jest-dom/vitest";
import { vi, beforeEach, afterEach } from "vitest";

const createDefaultFetchMock = () =>
  vi.fn(async (_input: RequestInfo | URL) => {
    return new Response(JSON.stringify({ code: "MOCK_NOT_FOUND" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  });

beforeEach(() => {
  vi.stubGlobal("fetch", createDefaultFetchMock());
});

afterEach(() => {
  vi.unstubAllGlobals();
});
