// Mirrors the Pydantic response models in src/askmydocs/api/main.py and
// src/askmydocs/models.py. Keep the two in step when a field changes.

export interface DocumentSummary {
  doc_id: string;
  filename: string;
  title: string;
  content_type: string;
  chunks: number;
  size_bytes: number;
}

export interface LibraryInfo {
  profile: string;
  embedder: string;
  reranker: string;
  generator: string;
  /** quote: answers are sentences quoted from the documents. generate: an LLM writes them. */
  answer_mode: "quote" | "generate";
  documents: number;
  max_upload_bytes: number;
  accepted_extensions: string[];
  samples_available: boolean;
  /** Whether library requests need the deployment's X-API-Key. */
  requires_api_key: boolean;
}

export interface Passage {
  chunk_id: string;
  doc_id: string;
  source: string;
  title: string;
  section: string;
  text: string;
}

export interface SamplesResponse {
  added: DocumentSummary[];
  /** Filename -> why it was not added. */
  rejected: Record<string, string>;
}

export interface Citation {
  marker: string;
  source: string;
  section: string;
  title: string;
  quote: string;
  score: number;
  chunk_id: string;
}

export interface RetrievedPassage {
  rank: number;
  source: string;
  section: string;
  chunk_id: string;
  score: number;
  retriever: "hybrid" | "dense" | "lexical";
  dense_rank: number | null;
  lexical_rank: number | null;
}

export interface AskRequest {
  question: string;
  /** Omit to search the whole library. */
  doc_ids?: string[];
  top_n?: number;
  include_retrieval?: boolean;
}

export interface AskResponse {
  question: string;
  answer: string;
  abstained: boolean;
  abstain_reason: string;
  citations: Citation[];
  confidence: number;
  grounding_score: number;
  model: string;
  prompt: string;
  /** Generation only. */
  latency_ms: number;
  /** The whole question: retrieval, reranking, and generation. */
  total_ms: number;
  input_tokens: number;
  output_tokens: number;
  retrieval: RetrievedPassage[] | null;
}
