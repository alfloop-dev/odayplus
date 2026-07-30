import "@testing-library/jest-dom/vitest";
import { vi, afterEach } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
