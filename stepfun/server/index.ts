import { createHash } from "node:crypto";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import express, { type ErrorRequestHandler, type Request, type RequestHandler, type Response } from "express";
import {
  FulltextIndexConfig,
  Schema,
  SeekdbClient,
  VectorIndexConfig,
  type Metadata,
} from "seekdb";
import { createServer as createViteServer } from "vite";
import {
  EMBEDDING_DIMENSION,
  type BenchmarkRequest,
  type IngestRequest,
  type SearchRequest,
  type SearchHit,
  type SourceSummary,
} from "../shared/contracts";

const PORT = Number(process.env.PORT ?? 4173);
const HOST = "127.0.0.1";
const DATABASE_PATH = resolve(process.env.SEEKDB_PATH ?? ".data/seekdb.db");
const DOCUMENT_COLLECTION = "stepfun_documents";
const BENCHMARK_COLLECTION = "stepfun_benchmark";
const MAX_DOCUMENT_CHUNKS = 1_000;
const MAX_BENCHMARK_DOCUMENTS = 100;
const PAGE_SIZE = 500;
const COLLECTION_SCHEMA = new Schema({
  fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: EMBEDDING_DIMENSION, distance: "cosine" },
    embeddingFunction: null,
  }),
});

const app = express();
app.disable("x-powered-by");
app.use((_request, response, next) => {
  response.setHeader("Cross-Origin-Opener-Policy", "same-origin");
  response.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  next();
});
app.use(express.json({ limit: "20mb" }));

await mkdir(resolve(DATABASE_PATH, ".."), { recursive: true });

const client = new SeekdbClient({
  path: DATABASE_PATH,
  database: "test",
});
const documents = await client.getOrCreateCollection({
  name: DOCUMENT_COLLECTION,
  schema: COLLECTION_SCHEMA,
  embeddingFunction: null,
});
const benchmark = await client.getOrCreateCollection({
  name: BENCHMARK_COLLECTION,
  schema: COLLECTION_SCHEMA,
  embeddingFunction: null,
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isVector(value: unknown): value is number[] {
  return Array.isArray(value)
    && value.length === EMBEDDING_DIMENSION
    && value.every((item) => typeof item === "number" && Number.isFinite(item));
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function sourceKey(sourceName: string): string {
  return sourceName.normalize("NFKC").trim().toLocaleLowerCase();
}

function sourceIdFor(sourceName: string): string {
  return createHash("sha256").update(sourceKey(sourceName)).digest("hex");
}

function getMetadataValue(metadata: Metadata | null | undefined, key: string): unknown {
  if (!metadata || !isRecord(metadata)) return undefined;
  return metadata[key];
}

function validateSourceName(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= 255;
}

function validateIngestRequest(value: unknown): value is IngestRequest {
  if (!isRecord(value) || !validateSourceName(value.sourceName)) return false;
  if (!Array.isArray(value.chunks) || value.chunks.length === 0 || value.chunks.length > MAX_DOCUMENT_CHUNKS) {
    return false;
  }
  return value.chunks.every((chunk) =>
    isRecord(chunk)
    && typeof chunk.text === "string"
    && chunk.text.trim().length > 0
    && chunk.text.length <= 20_000
    && Number.isInteger(chunk.index)
    && (chunk.index as number) >= 0
    && isVector(chunk.embedding),
  );
}

function validateSearchRequest(value: unknown): value is SearchRequest {
  return isRecord(value)
    && typeof value.query === "string"
    && value.query.trim().length > 0
    && value.query.length <= 2_000
    && isVector(value.embedding)
    && Number.isInteger(value.topK)
    && (value.topK as number) >= 1
    && (value.topK as number) <= 20;
}

function validateBenchmarkRequest(value: unknown): value is BenchmarkRequest {
  if (!isRecord(value) || !["fp32", "quantized"].includes(String(value.profile))) return false;
  if (!Array.isArray(value.documents) || value.documents.length === 0
      || value.documents.length > MAX_BENCHMARK_DOCUMENTS) return false;
  if (!Array.isArray(value.queries) || value.queries.length === 0 || value.queries.length > 100) return false;
  if (!Number.isInteger(value.topK) || (value.topK as number) < 1 || (value.topK as number) > 20) return false;

  const validDocuments = value.documents.every((document) =>
    isRecord(document)
    && typeof document.id === "string"
    && typeof document.text === "string"
    && document.text.length > 0
    && isVector(document.embedding),
  );
  const validQueries = value.queries.every((query) =>
    isRecord(query)
    && typeof query.id === "string"
    && typeof query.text === "string"
    && query.text.length > 0
    && isVector(query.embedding)
    && Array.isArray(query.relevantIds)
    && query.relevantIds.every((id) => typeof id === "string"),
  );
  return validDocuments && validQueries;
}

const asyncRoute = (
  handler: (request: Request, response: Response) => Promise<void>,
): RequestHandler => (request, response, next) => {
  void handler(request, response).catch(next);
};

app.get("/api/health", (_request, response) => {
  response.json({ status: "ready", database: "seekdb", embedding: "browser-only" });
});

app.get("/api/stats", asyncRoute(async (_request, response) => {
  const documentCount = await documents.count();
  const sourceMap = new Map<string, SourceSummary>();
  const pages = Math.ceil(documentCount / PAGE_SIZE);

  for (let page = 0; page < pages; page += 1) {
    const result = await documents.get({
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      include: ["metadatas"],
    });
    for (let index = 0; index < result.ids.length; index += 1) {
      const metadata = result.metadatas?.[index];
      const id = getMetadataValue(metadata, "sourceId");
      const name = getMetadataValue(metadata, "sourceName");
      if (typeof id !== "string" || typeof name !== "string") continue;
      const source = sourceMap.get(id) ?? { sourceId: id, sourceName: name, chunks: 0 };
      source.chunks += 1;
      sourceMap.set(id, source);
    }
  }

  response.json({
    documentCount,
    sources: [...sourceMap.values()].sort((left, right) => left.sourceName.localeCompare(right.sourceName)),
  });
}));

app.post("/api/ingest", asyncRoute(async (request, response) => {
  if (!validateIngestRequest(request.body)) {
    response.status(400).json({ error: `Invalid import. Each chunk must contain a ${EMBEDDING_DIMENSION}-dimension finite embedding.` });
    return;
  }

  const sourceName = request.body.sourceName.trim();
  const sourceId = sourceIdFor(sourceName);
  const chunks = request.body.chunks;
  const ids = chunks.map((chunk) => `doc_${sourceId}_${chunk.index}`);
  const previous = await documents.get({
    where: { sourceId: { $eq: sourceId } },
  });
  const nextIds = new Set(ids);
  const staleIds = previous.ids.filter((id) => !nextIds.has(id));
  const writeStarted = performance.now();

  await documents.upsert({
    ids,
    documents: chunks.map((chunk) => chunk.text),
    embeddings: chunks.map((chunk) => chunk.embedding),
    metadatas: chunks.map((chunk) => ({
      sourceId,
      sourceName,
      chunkIndex: chunk.index,
      updatedAt: new Date().toISOString(),
    })),
  });
  if (staleIds.length > 0) await documents.delete({ ids: staleIds });
  await documents.refresh_index();

  response.json({
    sourceId,
    sourceName,
    chunks: chunks.length,
    replacedChunks: previous.ids.length,
    removedChunks: staleIds.length,
    writeRefreshMs: performance.now() - writeStarted,
  });
}));

app.post("/api/search", asyncRoute(async (request, response) => {
  if (!validateSearchRequest(request.body)) {
    response.status(400).json({ error: `Invalid search. Query vectors must have ${EMBEDDING_DIMENSION} finite values and topK must be between 1 and 20.` });
    return;
  }

  const startedAt = performance.now();
  const result = await documents.hybridSearch({
    query: {
      whereDocument: { $contains: request.body.query.trim() },
      nResults: request.body.topK,
    },
    knn: {
      queryEmbeddings: [request.body.embedding],
      nResults: request.body.topK,
    },
    rank: { rrf: {} },
    nResults: request.body.topK,
    include: ["documents", "metadatas", "distances"],
  });
  const databaseMs = performance.now() - startedAt;
  const ids = result.ids[0] ?? [];
  const texts = result.documents?.[0] ?? [];
  const metadatas = result.metadatas?.[0] ?? [];
  const distances = result.distances?.[0] ?? [];
  const hits: SearchHit[] = ids.map((id, index) => {
    const metadata = metadatas[index];
    const chunkIndex = getMetadataValue(metadata, "chunkIndex");
    const distance = distances[index];
    return {
      id,
      text: texts[index] ?? "",
      sourceName: String(getMetadataValue(metadata, "sourceName") ?? "未知来源"),
      chunkIndex: typeof chunkIndex === "number" ? chunkIndex : 0,
      ...(typeof distance === "number" ? { distance } : {}),
    };
  });

  response.json({ hits, databaseMs });
}));

app.post("/api/source/delete", asyncRoute(async (request, response) => {
  if (!isRecord(request.body) || typeof request.body.sourceId !== "string" || !/^[a-f0-9]{64}$/.test(request.body.sourceId)) {
    response.status(400).json({ error: "Invalid source ID." });
    return;
  }
  const matches = await documents.get({
    where: { sourceId: { $eq: request.body.sourceId } },
  });
  if (matches.ids.length > 0) {
    await documents.delete({ ids: [...matches.ids] });
    await documents.refresh_index();
  }
  response.json({ deletedChunks: matches.ids.length });
}));

app.post("/api/benchmark", asyncRoute(async (request, response) => {
  if (!validateBenchmarkRequest(request.body)) {
    response.status(400).json({ error: "Invalid benchmark workload." });
    return;
  }

  const body = request.body;
  const profile = body.profile;
  const prefix = `eval_${profile}_`;
  const writeStarted = performance.now();
  await benchmark.upsert({
    ids: body.documents.map((document) => `${prefix}${document.id}`),
    documents: body.documents.map((document) => document.text),
    embeddings: body.documents.map((document) => document.embedding),
    metadatas: body.documents.map((document) => ({
      evalId: document.id,
      profile,
      sourceName: "内置检索质量测试集",
    })),
  });
  await benchmark.refresh_index();
  const writeRefreshMs = performance.now() - writeStarted;
  const searches: Array<{ queryId: string; ids: string[]; databaseMs: number }> = [];

  for (const query of body.queries) {
    const searchStarted = performance.now();
    const result = await benchmark.hybridSearch({
      query: {
        whereDocument: { $contains: query.text },
        where: { profile: { $eq: profile } },
        nResults: body.topK,
      },
      knn: {
        queryEmbeddings: [query.embedding],
        where: { profile: { $eq: profile } },
        nResults: body.topK,
      },
      rank: { rrf: {} },
      nResults: body.topK,
      include: ["metadatas"],
    });
    const databaseMs = performance.now() - searchStarted;
    const ids = (result.metadatas?.[0] ?? [])
      .map((metadata) => getMetadataValue(metadata, "evalId"))
      .filter((id): id is string => typeof id === "string");
    searches.push({ queryId: query.id, ids, databaseMs });
  }

  response.json({ writeRefreshMs, searches });
}));

const errorHandler: ErrorRequestHandler = (error, _request, response, _next) => {
  const message = getErrorMessage(error);
  console.error("Request failed:", message);
  if (response.headersSent) return;
  response.status(500).json({ error: message });
};
app.use(errorHandler);

const production = process.argv.includes("--production");
if (production) {
  const distPath = resolve("dist");
  app.use(express.static(distPath));
  app.use((_request, response) => response.sendFile(resolve(distPath, "index.html")));
} else {
  const vite = await createViteServer({
    configFile: resolve("vite.config.ts"),
    server: { middlewareMode: true },
  });
  app.use(vite.middlewares);
}

const server = app.listen(PORT, HOST, () => {
  console.info(`Stepfun local knowledge base: http://${HOST}:${PORT}`);
  console.info(`seekdb database: ${DATABASE_PATH}`);
});

async function shutdown(): Promise<void> {
  server.close();
  await client.close();
}

process.once("SIGINT", () => void shutdown());
process.once("SIGTERM", () => void shutdown());
