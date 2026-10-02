export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  // 20 MB, not 20.0 MB; 1.5 MB stays 1.5 MB.
  return `${Number((bytes / (1024 * 1024)).toFixed(1))} MB`;
}

export function formatDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** Sources are server paths; the user only cares about the filename. */
export function basename(source: string): string {
  return source.split(/[\\/]/).pop() ?? source;
}

/** Why the system declined, in words a user can act on. Keys match the API's abstain_reason. */
export const ABSTAIN_EXPLANATIONS: Record<string, string> = {
  no_results: "Nothing in the selected documents matched this question at all.",
  low_relevance: "The closest passage wasn't relevant enough to answer from.",
  question_not_covered: "A key term in your question doesn't appear anywhere in your documents.",
  model_refusal: "The model judged the retrieved passages insufficient to answer.",
  no_citations: "The generated answer couldn't be tied to a source, so it was withheld.",
  low_grounding: "The answer wasn't supported closely enough by the passages it cited.",
  empty_response: "The model returned nothing.",
};
