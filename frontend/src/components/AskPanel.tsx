import { useState, type FormEvent } from "react";

import { useAddSamples, useAsk, useLibraryInfo } from "../api/hooks";
import type { DocumentSummary } from "../api/types";
import { ALL_DOCUMENTS } from "../constants";
import { AnswerView } from "./AnswerView";

interface Props {
  documents: DocumentSummary[];
  loading: boolean;
  loadError: Error | null;
  scope: string;
  onScopeChange: (scope: string) => void;
}

export function AskPanel({ documents, loading, loadError, scope, onScopeChange }: Props) {
  const [question, setQuestion] = useState("");
  const ask = useAsk();

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || ask.isPending) return;
    ask.mutate({ question: text, doc_ids: scope === ALL_DOCUMENTS ? undefined : [scope] });
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
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ask anything about your documents…"
                aria-label="Question"
                maxLength={2000}
                className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-4 py-2.5 shadow-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/30 focus:outline-none dark:border-slate-700 dark:bg-slate-900"
              />
              <button
                type="submit"
                disabled={!question.trim() || ask.isPending}
                className="rounded-lg bg-indigo-600 px-5 py-2.5 font-medium text-white hover:bg-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 dark:focus-visible:ring-offset-slate-950"
              >
                Ask
              </button>
            </div>
          </form>

          <div aria-live="polite" aria-busy={ask.isPending}>
            {ask.isPending ? (
              <p className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
                <span className="size-4 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
                Searching your documents…
              </p>
            ) : ask.error ? (
              <ErrorCard message={ask.error.message} />
            ) : ask.data ? (
              <AnswerView answer={ask.data} />
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

function EmptyLibrary() {
  const info = useLibraryInfo();
  const samples = useAddSamples();

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-lg font-medium">Start by adding a document</p>
      <p className="mt-1 text-slate-600 dark:text-slate-400">
        Upload a PDF, Markdown, text, or HTML file in the sidebar. Questions are answered only from
        what you upload, and every answer shows the passage it came from.
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
