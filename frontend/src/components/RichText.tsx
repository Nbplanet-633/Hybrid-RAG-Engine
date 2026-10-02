import type { ReactNode } from "react";

// Answers and quotes come straight from the documents, so they carry inline
// markdown (**bold**, `code`) plus the generator's [S1] citation markers. Parse
// just those into React elements rather than rendering HTML: document text is
// untrusted, and this never builds markup from it.
const TOKEN = /\*\*(.+?)\*\*|`([^`]+)`|\[S(\d+)\]/g;

interface Props {
  text: string;
  /** Called with a marker like "S1" when its chip is clicked. Omit to render markers as plain text. */
  onMarker?: (marker: string) => void;
}

export function RichText({ text, onMarker }: Props) {
  return <>{parse(text, onMarker, "t")}</>;
}

function parse(text: string, onMarker: Props["onMarker"], keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    const index = match.index ?? 0;
    const key = `${keyPrefix}-${index}`;
    if (index > last) nodes.push(text.slice(last, index));
    const [whole, bold, code, marker] = match;
    if (bold !== undefined) {
      // Bold often wraps a whole sentence that itself holds `code`, so recurse.
      nodes.push(<strong key={key}>{parse(bold, onMarker, key)}</strong>);
    } else if (code !== undefined) {
      nodes.push(
        <code
          key={key}
          className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.9em] dark:bg-slate-800"
        >
          {code}
        </code>,
      );
    } else if (marker !== undefined && onMarker) {
      nodes.push(
        <button
          key={key}
          type="button"
          onClick={() => onMarker(`S${marker}`)}
          className="mx-0.5 inline-flex items-center rounded bg-indigo-100 px-1.5 align-baseline font-mono text-xs font-medium text-indigo-700 hover:bg-indigo-200 dark:bg-indigo-500/20 dark:text-indigo-300 dark:hover:bg-indigo-500/30"
          aria-label={`Go to source ${marker}`}
        >
          S{marker}
        </button>,
      );
    } else {
      nodes.push(whole);
    }
    last = index + whole.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}
