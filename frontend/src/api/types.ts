// The API's types, generated from the server's OpenAPI schema. Don't hand-write
// shapes here: change the Pydantic models, then regenerate:
//
//   python scripts/export_openapi.py      (repo root: writes frontend/openapi.json)
//   npm run api:types                     (frontend/: writes src/api/schema.gen.ts)
//
// CI fails when either file is stale, so an API change the frontend hasn't
// absorbed is a type error at build time, not a bug in the browser.

import type { components } from "./schema.gen";

type Schemas = components["schemas"];

export type DocumentSummary = Schemas["DocumentSummary"];
export type LibraryInfo = Schemas["LibraryInfo"];
export type Passage = Schemas["PassageOut"];
export type SamplesResponse = Schemas["SamplesResponse"];
export type Citation = Schemas["CitationOut"];
export type RetrievedPassage = Schemas["RetrievedOut"];
export type AskRequest = Schemas["LibraryAskRequest"];
export type AskResponse = Schemas["AskResponse"];
