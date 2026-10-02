import { useRef, useState, type FormEvent } from "react";

import { api } from "../api/client";
import { useAddSamples, useLibraryInfo } from "../api/hooks";
import type { AskResponse, DocumentSummary } from "../api/types";
import { ALL_DOCUMENTS } from "../constants";
import { AnswerView } from "./AnswerView";

interface Props {
  documents: DocumentSummary[];
  loading: boolean;
  loadError: Error | null;
  scope: string;
  onScopeChange: (scope: string) => void;
}

interface Turn {
  id: number;
  question: string;
  /** What was searched, as the user saw it when asking: "All documents" or a filename. */
  scopeLabel: string;
  status: "pending" | "done" | "error";
  response?: AskResponse;
  error?: string;
}

let turnSeq = 0;

export function AskPanel({ documents, loading, loadError, scope, onScopeChange }: Props) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const pending = turns.some((turn) => turn.status === "pending");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    // One question at a time: the server answers them one by one anyway.
    if (!text || pending) return;

    const id = ++turnSeq;
    const scopeLabel =
      scope === ALL_DOCUMENTS
        ? "All documents"
        : (documents.find((doc) => doc.doc_id === scope)?.filename ?? "One document");
    setTurns((current) => [{ id, question: text, scopeLabel, status: "pending" }, ...current]);
    setQuestion("");

    const update = (patch: Partial<Turn>) =>
      setTurns((current) => current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)));
    try {
      const response = await api.ask({
        question: text,
        doc_ids: scope === ALL_DOCUMENTS ? undefined : [scope],
        include_retrieval: true,
      });
      update({ status: "done", response });
    } catch (error) {
      update({ status: "error", error: (error as Error).message });
    }
    input.current?.focus();
  }

  return (
    <div className="flex flex-col gap-6">
      <h2 className="text-3xl font-semibold tracking-tight">Ask your documents</h2>

      {loadError ? (
        <ErrorCard message={loadError.message} />
      ) : loading ? (
        <div className="h-24 animate-pulse rounded-xl bg-slate-200/60 dark:bg-slate-800/60" />
      ) : !documents.length ? (
        <EmptyLibrary />
      ) : (
        <>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-slate-600 dark:text-slate-300">Search in</span>
              <select
                value={scope}
                onChange={(event) => onScopeChange(event.target.value)}
                className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
              >
                <option value={ALL_DOCUMENTS}>All documents ({documents.length})</option>
                {documents.map((doc) => (
                  <option key={doc.doc_id} value={doc.doc_id}>
                    {doc.filename}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex gap-2">
              <input
                ref={input}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ask anything about your documents…"
                aria-label="Question"
                maxLength={2000}
                className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-4 py-2.5 shadow-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none dark:border-slate-700 dark:bg-slate-900"
              />
              <button
                type="submit"
                disabled={!question.trim() || pending}
                className="rounded-lg bg-indigo-600 px-5 py-2.5 font-medium text-white hover:bg-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 dark:focus-visible:ring-offset-slate-950"
              >
                Ask
              </button>
            </div>
          </form>

          {turns.length > 0 && (
            <section aria-label="Conversation" className="flex flex-col gap-8">
              <div className="flex items-center justify-between gap-4 border-b border-slate-200 pb-2 dark:border-slate-800">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Newest first. Each question is answered on its own; earlier ones aren't used as
                  context.
                </p>
                <button
                  type="button"
                  onClick={() => setTurns([])}
                  disabled={pending}
                  className="shrink-0 text-xs text-slate-500 hover:text-slate-900 disabled:opacity-50 dark:text-slate-400 dark:hover:text-slate-100"
                >
                  Clear
                </button>
              </div>
              {turns.map((turn) => (
                <TurnView key={turn.id} turn={turn} />
              ))}
            </section>
          )}
        </>
      )}
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  return (
    <article className="flex flex-col gap-3" aria-busy={turn.status === "pending"}>
      <header>
        <p className="text-lg font-medium">{turn.question}</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">Searched: {turn.scopeLabel}</p>
      </header>
      <div aria-live="polite">
        {turn.status === "pending" ? (
          <p className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
            <span className="size-4 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
            Searching your documents…
          </p>
        ) : turn.status === "error" ? (
          <ErrorCard message={turn.error ?? "Something went wrong."} />
        ) : turn.response ? (
          <AnswerView answer={turn.response} turnId={turn.id} />
        ) : null}
      </div>
    </article>
  );
}

function EmptyLibrary() {
  const info = useLibraryInfo();
  const samples = useAddSamples();

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-lg font-medium">Start by adding a document</p>
      <p className="mt-1 text-slate-600 dark:text-slate-400">
        Upload a PDF, Markdown, text, or HTML file under <strong>Your documents</strong>. Questions
        are answered only from what you upload, and every answer shows the passage it came from.
      </p>
      {info.data?.samples_available && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => samples.mutate()}
            disabled={samples.isPending}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
          >
            {samples.isPending ? "Loading samples…" : "Try with sample documents"}
          </button>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            Loads 8 example API docs for a fictional payments company.
          </p>
          {samples.error && (
            <p className="mt-2 text-sm text-red-600 dark:text-red-400">{samples.error.message}</p>
          )}
        </div>
      )}
    </div>
  );
}

function ErrorCard({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300"
    >
      {message}
    </div>
  );
}
