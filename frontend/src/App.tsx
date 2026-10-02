import { useState } from "react";

import { useDocuments, useLibraryInfo } from "./api/hooks";
import { useApiKey } from "./apiKey";
import { ApiKeyPrompt } from "./components/ApiKeyPrompt";
import { AskPanel } from "./components/AskPanel";
import { Sidebar } from "./components/Sidebar";
import { ALL_DOCUMENTS } from "./constants";

export function App() {
  const info = useLibraryInfo();
  const { key, rejected } = useApiKey();

  // Wait for /library/info before loading anything keyed: it says whether a
  // key is needed, and requesting first would only collect 401s.
  if (info.isPending) {
    return <div className="min-h-screen" aria-busy="true" />;
  }
  if (info.data?.requires_api_key && !key) {
    return <ApiKeyPrompt rejected={rejected} />;
  }
  return <Workspace />;
}

function Workspace() {
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
