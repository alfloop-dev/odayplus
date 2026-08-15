import { createInitialOperatorState, cloneOperatorState, operatorReducer } from "./state";
import type {
  OperatorAction,
  OperatorConsoleAdapter,
  OperatorRoleId,
  OperatorState,
  OperatorStateListener,
} from "./types";

const DEFAULT_SESSION_KEY = "oday-plus.operator-console.fixture-state.v1";

export function createInMemoryOperatorAdapter(initialRoleId?: OperatorRoleId): OperatorConsoleAdapter {
  let state = createInitialOperatorState(initialRoleId);
  const listeners = new Set<OperatorStateListener>();

  const publish = () => {
    const snapshot = cloneOperatorState(state);
    listeners.forEach((listener) => listener(snapshot));
  };

  return {
    async loadState(roleId?: OperatorRoleId) {
      if (roleId && roleId !== state.roleId) {
        state = createInitialOperatorState(roleId);
        publish();
      }

      return cloneOperatorState(state);
    },
    async dispatch(action: OperatorAction) {
      state = operatorReducer(state, action);
      publish();
      return cloneOperatorState(state);
    },
    async resetState(roleId?: OperatorRoleId) {
      state = createInitialOperatorState(roleId ?? state.roleId);
      publish();
      return cloneOperatorState(state);
    },
    async saveState(nextState: OperatorState) {
      state = cloneOperatorState(nextState);
      publish();
      return cloneOperatorState(state);
    },
    subscribe(listener: OperatorStateListener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export function createSessionOperatorAdapter(
  storageKey = DEFAULT_SESSION_KEY,
  initialRoleId?: OperatorRoleId,
): OperatorConsoleAdapter {
  const fallback = createInMemoryOperatorAdapter(initialRoleId);
  const listeners = new Set<OperatorStateListener>();
  let state = readSessionState(storageKey) ?? createInitialOperatorState(initialRoleId);

  const publish = () => {
    const snapshot = cloneOperatorState(state);
    listeners.forEach((listener) => listener(snapshot));
  };

  const persist = () => {
    if (!canUseSessionStorage()) return;

    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(state));
    } catch {
      // Session persistence is a convenience for the demo adapter; memory state remains authoritative.
    }
  };

  if (!canUseSessionStorage()) {
    return fallback;
  }

  return {
    async loadState(roleId?: OperatorRoleId) {
      if (roleId && roleId !== state.roleId) {
        state = createInitialOperatorState(roleId);
        persist();
        publish();
      }

      return cloneOperatorState(state);
    },
    async dispatch(action: OperatorAction) {
      state = operatorReducer(state, action);
      persist();
      publish();
      return cloneOperatorState(state);
    },
    async resetState(roleId?: OperatorRoleId) {
      state = createInitialOperatorState(roleId ?? state.roleId);
      persist();
      publish();
      return cloneOperatorState(state);
    },
    async saveState(nextState: OperatorState) {
      state = cloneOperatorState(nextState);
      persist();
      publish();
      return cloneOperatorState(state);
    },
    subscribe(listener: OperatorStateListener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export const fixtureOperatorAdapter = createInMemoryOperatorAdapter();

function readSessionState(storageKey: string): OperatorState | undefined {
  if (!canUseSessionStorage()) return undefined;

  try {
    const raw = window.sessionStorage.getItem(storageKey);
    return raw ? (JSON.parse(raw) as OperatorState) : undefined;
  } catch {
    return undefined;
  }
}

function canUseSessionStorage(): boolean {
  return typeof window !== "undefined" && typeof window.sessionStorage !== "undefined";
}
