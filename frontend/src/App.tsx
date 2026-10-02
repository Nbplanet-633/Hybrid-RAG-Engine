import { useState } from "react";

import { useDocuments } from "./api/hooks";
import { AskPanel } from "./components/AskPanel";
import { Sidebar } from "./components/Sidebar";
import { ALL_DOCUMENTS } from "./constants";

export function App() {
  const documents = useDocuments();
  const [scope, setScope] = useState<string>(ALL_DOCUMENTS);

  // A deleted document can't stay selected; fall back to searching everything.
  const docs = documents.data ?? [];
  const activeScope =
    scope !== ALL_DOCUMENTS && !docs.some((d) => d.doc_id === scope) ? ALL_DOCUMENTS : scope;

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <Sidebar />
      <main className="flex-1 px-4 py-8 md:px-10 md:py-12">
        <div className="mx-auto max-w-3xl">
          <AskPanel
            documents={docs}
            loading={documents.isPending}
            loadError={documents.error}
            scope={activeScope}
            onScopeChange={setScope}
          />
        </div>
      </main>
    </div>
  );
}
