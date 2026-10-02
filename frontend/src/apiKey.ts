import { useSyncExternalStore } from "react";

// The deployment's shared X-API-Key, for servers started with ASKMYDOCS_API_KEY.
// "Remember" keeps it in localStorage; otherwise it lives in sessionStorage and
// is gone when the tab closes. Storage access is guarded: it throws in some
// privacy modes, and the app must still work for the current visit.

const KEY = "askmydocs.apiKey";

interface State {
  key: string | null;
  /** Set when the server refused the stored key, so the prompt can say why. */
  rejected: boolean;
}

let state: State = { key: load(), rejected: false };
const listeners = new Set<() => void>();

function load(): string | null {
  try {
    return localStorage.getItem(KEY) ?? sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function emit(next: State) {
  state = next;
  listeners.forEach((listener) => listener());
}

function forgetStored() {
  try {
    localStorage.removeItem(KEY);
    sessionStorage.removeItem(KEY);
  } catch {
    // Nothing stored that we can reach.
  }
}

export const apiKey = {
  get: () => state.key,

  set(key: string, remember: boolean) {
    forgetStored();
    try {
      (remember ? localStorage : sessionStorage).setItem(KEY, key);
    } catch {
      // Kept in memory only.
    }
    emit({ key, rejected: false });
  },

  forget() {
    forgetStored();
    emit({ key: null, rejected: false });
  },

  /** The server answered 401: drop the key and ask again. */
  reject() {
    forgetStored();
    // Only a key that was actually sent can have been rejected.
    emit({ key: null, rejected: state.key !== null });
  },

  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

export function useApiKey(): State {
  return useSyncExternalStore(apiKey.subscribe, () => state);
}
