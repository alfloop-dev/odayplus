import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  lifecycleBackoffDelay,
  useIntakeLifecycle,
  type IntakeLifecycleSnapshot,
  type LifecycleSubscription,
} from "../useIntakeLifecycle";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function snapshot(
  version: number,
  stage = "SUBMITTED",
  intakeId = "INT-001",
  sequence = version,
): IntakeLifecycleSnapshot {
  return {
    record: {
      id: intakeId,
      originalUrl: "https://example.com/listing/1",
      canonicalUrl: "https://example.com/listing/1",
      submitter: "staff-1",
      owner: "staff-1",
      heatZoneId: null,
      stage,
      sourceId: "synthetic",
      policy: "APPROVED_RETRIEVAL",
      policyLabel: "核准",
      policyReason: "policy",
      rawSnapshot: null,
      snapshotId: "SNAP-1",
      capturedAt: "2026-07-23T12:00:00Z",
      parserVersion: "parser-1",
      correlationId: "CORR-1",
      parsedFields: {},
      matchResult: null,
      auditEvents: [],
      version,
    } as IntakeLifecycleSnapshot["record"],
    intake_history: [],
    assignment: null,
    assignment_history: [],
    sla: null,
    sla_history: [],
    decisions: [],
    decision_history: [],
    promotion: null,
    promotion_history: [],
    jobs: [],
    job_history: [],
    allowed_actions: [],
    refreshed_at: `2026-07-23T12:00:0${version}Z`,
    sequence,
    updated_at: `2026-07-23T12:00:0${version}Z`,
    version,
  };
}

type ProbeProps = {
  intakeId?: string;
  loadSnapshot: Parameters<typeof useIntakeLifecycle>[0]["loadSnapshot"];
  subscribe?: LifecycleSubscription;
};

function Probe({ intakeId = "INT-001", loadSnapshot, subscribe }: ProbeProps) {
  const state = useIntakeLifecycle({
    activeIntervalMs: 1_000,
    intakeId,
    maxBackoffMs: 8_000,
    loadSnapshot,
    subscribe,
  });
  return (
    <div>
      <span data-testid="version">{state.snapshot?.version ?? "none"}</span>
      <span data-testid="mode">{state.mode}</span>
      <span data-testid="failures">{state.consecutiveFailures}</span>
      <span data-testid="error">{state.error?.message ?? ""}</span>
      <button onClick={() => state.refresh("MANUAL")} type="button">
        refresh
      </button>
    </div>
  );
}

let container: HTMLDivElement;
let root: Root;

async function renderProbe(props: ProbeProps) {
  await act(async () => {
    root.render(<Probe {...props} />);
    await Promise.resolve();
  });
}

function text(testId: string): string {
  return container.querySelector(`[data-testid="${testId}"]`)?.textContent ?? "";
}

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: state,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.useFakeTimers();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  setVisibility("visible");
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("useIntakeLifecycle", () => {
  it("reads initial and timer-driven server snapshots without optimistic state", async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce(snapshot(1, "SUBMITTED"))
      .mockResolvedValueOnce(snapshot(2, "PARSING"));
    await renderProbe({ loadSnapshot: load });

    expect(load).toHaveBeenCalledTimes(1);
    expect(text("version")).toBe("1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(load).toHaveBeenCalledTimes(2);
    expect(load.mock.calls[1][0].reason).toBe("POLL");
    expect(text("version")).toBe("2");
  });

  it("backs off after errors and resets after a successful persisted read", async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce(snapshot(2, "MATCHING"));
    await renderProbe({ loadSnapshot: load });

    expect(text("failures")).toBe("1");
    expect(text("error")).toBe("temporary");
    expect(lifecycleBackoffDelay(1, 1_000, 8_000)).toBe(2_000);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_999);
    });
    expect(load).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(load).toHaveBeenCalledTimes(2);
    expect(text("failures")).toBe("0");
    expect(text("version")).toBe("2");
  });

  it("stops network reads while hidden and refreshes immediately when visible", async () => {
    const load = vi.fn().mockResolvedValue(snapshot(1));
    await renderProbe({ loadSnapshot: load });
    expect(load).toHaveBeenCalledTimes(1);

    act(() => setVisibility("hidden"));
    expect(text("mode")).toBe("HIDDEN");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(load).toHaveBeenCalledTimes(1);

    await act(async () => setVisibility("visible"));
    expect(load).toHaveBeenCalledTimes(2);
    expect(load.mock.calls[1][0].reason).toBe("VISIBLE");
  });

  it("accepts subscription snapshots and releases the subscription on unmount", async () => {
    let handlers: Parameters<LifecycleSubscription>[0] | null = null;
    const unsubscribe = vi.fn();
    const subscribe: LifecycleSubscription = (nextHandlers) => {
      handlers = nextHandlers;
      return unsubscribe;
    };
    const load = vi.fn().mockResolvedValue(snapshot(1));
    await renderProbe({ loadSnapshot: load, subscribe });

    await act(async () => {
      handlers?.onSnapshot(snapshot(4, "READY"));
    });
    expect(text("version")).toBe("4");
    expect(text("mode")).toBe("SUBSCRIBED");

    act(() => root.unmount());
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    root = createRoot(container);
  });

  it("rejects a stale poll that resolves after a newer subscription snapshot", async () => {
    let handlers: Parameters<LifecycleSubscription>[0] | null = null;
    const subscribe: LifecycleSubscription = (nextHandlers) => {
      handlers = nextHandlers;
      return vi.fn();
    };
    const delayedPoll = deferred<IntakeLifecycleSnapshot>();
    const load = vi
      .fn()
      .mockResolvedValueOnce(snapshot(1))
      .mockReturnValueOnce(delayedPoll.promise);
    await renderProbe({ loadSnapshot: load, subscribe });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    await act(async () => {
      handlers?.onSnapshot(snapshot(4, "READY"));
    });
    expect(text("version")).toBe("4");

    await act(async () => {
      delayedPoll.resolve(snapshot(2, "PARSING"));
      await delayedPoll.promise;
    });
    expect(text("version")).toBe("4");
  });

  it("rejects stale subscription snapshots by version, sequence, and updated time", async () => {
    let handlers: Parameters<LifecycleSubscription>[0] | null = null;
    const subscribe: LifecycleSubscription = (nextHandlers) => {
      handlers = nextHandlers;
      return vi.fn();
    };
    const load = vi.fn().mockResolvedValue(snapshot(2));
    await renderProbe({ loadSnapshot: load, subscribe });

    await act(async () => {
      handlers?.onSnapshot(snapshot(5, "READY", "INT-001", 10));
      handlers?.onSnapshot({
        ...snapshot(5, "MATCHING", "INT-001", 9),
        updated_at: "2026-07-23T12:00:04Z",
      });
      handlers?.onSnapshot({
        ...snapshot(5, "MATCHING", "INT-001", 10),
        updated_at: "2026-07-23T12:00:04Z",
      });
      handlers?.onSnapshot(snapshot(4, "PARSING", "INT-001", 11));
    });

    expect(text("version")).toBe("5");
  });

  it("unsubscribes and rejects old-record events when intake identity changes", async () => {
    let firstHandlers: Parameters<LifecycleSubscription>[0] | null = null;
    let secondHandlers: Parameters<LifecycleSubscription>[0] | null = null;
    const firstUnsubscribe = vi.fn();
    const secondUnsubscribe = vi.fn();
    const firstSubscribe: LifecycleSubscription = (handlers) => {
      firstHandlers = handlers;
      return firstUnsubscribe;
    };
    const secondSubscribe: LifecycleSubscription = (handlers) => {
      secondHandlers = handlers;
      return secondUnsubscribe;
    };
    const firstLoad = vi.fn().mockResolvedValue(snapshot(1, "SUBMITTED", "INT-001"));
    const secondLoad = vi.fn().mockResolvedValue(snapshot(1, "SUBMITTED", "INT-002"));
    await renderProbe({ intakeId: "INT-001", loadSnapshot: firstLoad, subscribe: firstSubscribe });

    await renderProbe({
      intakeId: "INT-002",
      loadSnapshot: secondLoad,
      subscribe: secondSubscribe,
    });
    expect(firstUnsubscribe).toHaveBeenCalledTimes(1);
    expect(text("version")).toBe("1");

    await act(async () => {
      firstHandlers?.onSnapshot(snapshot(8, "READY", "INT-001"));
      secondHandlers?.onSnapshot(snapshot(2, "PARSING", "INT-002"));
    });
    expect(text("version")).toBe("2");

    act(() => root.unmount());
    expect(secondUnsubscribe).toHaveBeenCalledTimes(1);
    root = createRoot(container);
  });

  it("rejects events from a replaced subscription for the same intake", async () => {
    let firstHandlers: Parameters<LifecycleSubscription>[0] | null = null;
    let secondHandlers: Parameters<LifecycleSubscription>[0] | null = null;
    const firstUnsubscribe = vi.fn();
    const firstSubscribe: LifecycleSubscription = (handlers) => {
      firstHandlers = handlers;
      return firstUnsubscribe;
    };
    const secondSubscribe: LifecycleSubscription = (handlers) => {
      secondHandlers = handlers;
      return vi.fn();
    };
    const load = vi.fn().mockResolvedValue(snapshot(1));
    await renderProbe({ loadSnapshot: load, subscribe: firstSubscribe });
    await renderProbe({ loadSnapshot: load, subscribe: secondSubscribe });
    expect(firstUnsubscribe).toHaveBeenCalledTimes(1);

    await act(async () => {
      firstHandlers?.onSnapshot(snapshot(8, "READY"));
      secondHandlers?.onSnapshot(snapshot(3, "MATCHING"));
    });
    expect(text("version")).toBe("3");
  });
});
