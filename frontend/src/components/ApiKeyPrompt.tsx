import { useState, type FormEvent } from "react";

import { apiKey } from "../apiKey";

/** Shown instead of the app when the server requires a key and none is set. */
export function ApiKeyPrompt({ rejected }: { rejected: boolean }) {
  const [value, setValue] = useState("");
  const [remember, setRemember] = useState(false);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (value.trim()) apiKey.set(value.trim(), remember);
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900"
      >
        <h1 className="text-xl font-semibold tracking-tight">Ask My Docs</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          This server requires an API key.
        </p>
        {rejected && (
          <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
            The server didn't accept that key. Check it and try again.
          </p>
        )}
        <label className="mt-4 flex flex-col gap-1 text-sm">
          <span className="font-medium">API key</span>
          <input
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoComplete="off"
            autoFocus
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
          />
        </label>
        <label className="mt-3 flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
          <input
            type="checkbox"
            checked={remember}
            onChange={(event) => setRemember(event.target.checked)}
          />
          Remember on this device
        </label>
        <button
          type="submit"
          disabled={!value.trim()}
          className="mt-5 w-full rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          Continue
        </button>
        <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
          It's the value the server was started with as <code>ASKMYDOCS_API_KEY</code>.
        </p>
      </form>
    </main>
  );
}
