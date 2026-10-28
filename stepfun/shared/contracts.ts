export const EMBEDDING_DIMENSION = 512;

export interface IngestChunk {
  text: string;
  index: number;
  embedding: number[];
}

export interface IngestRequest {
  sourceName: string;
  chunks: IngestChunk[];
}

export interface SearchRequest {
  query: string;
  embedding: number[];
  topK: number;
}

export interface SearchHit {
  id: string;
  text: string;
  sourceName: string;
  chunkIndex: number;
  distance?: number;
}

export interface SourceSummary {
  sourceId: string;
  sourceName: string;
  chunks: number;
}

export interface StatsResponse {
  documentCount: number;
  sources: SourceSummary[];
}

export interface SearchResponse {
  hits: SearchHit[];
  databaseMs: number;
}

export interface BenchmarkDocument {
  id: string;
  text: string;
  embedding: number[];
}

export interface BenchmarkQuery {
  id: string;
  text: string;
  embedding: number[];
  relevantIds: string[];
}

export interface BenchmarkRequest {
  profile: "fp32" | "quantized";
  documents: BenchmarkDocument[];
  queries: BenchmarkQuery[];
  topK: number;
}

export interface BenchmarkResponse {
  writeRefreshMs: number;
  searches: Array<{
    queryId: string;
    ids: string[];
    databaseMs: number;
  }>;
}

export interface ApiError {
  error: string;
}
