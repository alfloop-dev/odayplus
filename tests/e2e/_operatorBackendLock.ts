import { mkdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { test } from "@playwright/test";

/**
 * Cross-file mutex for the shared Operator Console backend (ODP-OC-R5-011).
 *
 * playwright.config.ts sets `fullyParallel`, so spec FILES run concurrently in
 * separate worker processes against ONE FastAPI process. The operator network
 * listing service is a singleton pinned to tenant-a (see
 * dependencies.OPERATOR_TENANT_ID), so it cannot be isolated per spec by tenant
 * or by session. Every file that POSTs `.../network-listings/reset` therefore
 * wipes the state of whichever other file is mid-test.
 *
 * Playwright's `serial` mode only orders tests *within* a file, and there is no
 * built-in cross-file exclusion, so the shared resource needs an explicit lock.
 * Files that mutate the operator backend take this lock for their whole run;
 * everything else in the suite still runs in parallel.
 *
 * The lock is a directory because `mkdir` is atomic across processes: exactly
 * one worker can create it, and the rest poll. A lock left behind by a killed
 * worker goes stale and is reclaimed, so a crash cannot wedge the suite.
 */

const LOCK_PATH = join(tmpdir(), "odp-operator-backend-e2e.lock");

/** Longer than the slowest reset-owning spec file, incl. cold dev-server compile. */
const STALE_AFTER_MS = 10 * 60_000;
const ACQUIRE_TIMEOUT_MS = 12 * 60_000;
const POLL_INTERVAL_MS = 250;

let heldByThisWorker = false;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Reclaim a lock whose owning worker died without releasing it. */
function clearIfStale(): void {
  try {
    const age = Date.now() - statSync(LOCK_PATH).mtimeMs;
    if (age > STALE_AFTER_MS) rmSync(LOCK_PATH, { force: true, recursive: true });
  } catch {
    // Vanished between the failed mkdir and here — the next attempt will win it.
  }
}

export async function acquireOperatorBackendLock(): Promise<void> {
  // Waiting for another spec file to finish legitimately outlasts the default
  // 30s hook/test timeout, and blowing that would defeat the whole point of the
  // lock. Applies to the calling hook or test, not the suite.
  test.setTimeout(ACQUIRE_TIMEOUT_MS + 60_000);

  const deadline = Date.now() + ACQUIRE_TIMEOUT_MS;
  for (;;) {
    try {
      mkdirSync(LOCK_PATH);
      // Owner recorded for debugging only; correctness rests on mkdir atomicity.
      writeFileSync(join(LOCK_PATH, "owner"), `pid ${process.pid}\n`);
      heldByThisWorker = true;
      return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      if (Date.now() > deadline) {
        throw new Error(
          `Timed out after ${ACQUIRE_TIMEOUT_MS}ms waiting for the operator backend lock at ${LOCK_PATH}. ` +
            `If no e2e run is in progress, delete it.`,
        );
      }
      clearIfStale();
      await sleep(POLL_INTERVAL_MS);
    }
  }
}

export function releaseOperatorBackendLock(): void {
  // Only the holder may release: a worker that timed out must never delete the
  // lock a different worker is legitimately holding.
  if (!heldByThisWorker) return;
  heldByThisWorker = false;
  rmSync(LOCK_PATH, { force: true, recursive: true });
}
