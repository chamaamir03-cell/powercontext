import { createHash, randomUUID } from "node:crypto";

const baseUrl = process.env.STEPFUN_URL ?? "http://127.0.0.1:4173";
const sourceName = `acceptance-${randomUUID()}.txt`;
const sourceId = createHash("sha256")
  .update(sourceName.normalize("NFKC").trim().toLocaleLowerCase())
  .digest("hex");
const vector = Array.from({ length: 512 }, (_, index) => index === 0 ? 1 : 0);
let mayHaveImported = false;

async function request(path, body) {
  const response = await fetch(new URL(path, baseUrl), {
    method: body ? "POST" : "GET",
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(`${path} failed with HTTP ${response.status}: ${result.error ?? "unknown error"}`);
  }
  return result;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

try {
  const health = await request("/api/health");
  assert(health.status === "ready" && health.database === "seekdb", "seekdb health check failed.");

  const initialStats = await request("/api/stats");
  assert(Number.isInteger(initialStats.documentCount), "seekdb stats response is invalid.");

  mayHaveImported = true;
  const imported = await request("/api/ingest", {
    sourceName,
    chunks: [
      { index: 0, text: "Acceptance test checks seekdb vector and full-text hybrid retrieval.", embedding: vector },
      { index: 1, text: "This stale acceptance chunk must be deleted during incremental replacement.", embedding: vector },
    ],
  });
  assert(imported.sourceId === sourceId, "ingest returned an unexpected source ID.");
  assert(imported.chunks === 2, "initial import did not write both chunks.");

  const statsAfterImport = await request("/api/stats");
  const importedSource = statsAfterImport.sources.find((source) => source.sourceId === sourceId);
  assert(importedSource?.chunks === 2, "imported chunks are missing from seekdb stats.");

  const search = await request("/api/search", {
    query: "hybrid retrieval",
    embedding: vector,
    topK: 5,
  });
  assert(
    search.hits.some((hit) => hit.sourceName === sourceName && hit.text.includes("hybrid retrieval")),
    "seekdb hybrid search did not return the imported matching content.",
  );

  const replaced = await request("/api/ingest", {
    sourceName,
    chunks: [
      { index: 0, text: "Updated acceptance content remains searchable after index refresh.", embedding: vector },
    ],
  });
  assert(replaced.removedChunks === 1, "incremental import did not remove the stale chunk.");

  const statsAfterReplace = await request("/api/stats");
  const updatedSource = statsAfterReplace.sources.find((source) => source.sourceId === sourceId);
  assert(updatedSource?.chunks === 1, "incremental replacement left stale chunks in seekdb.");

  await request("/api/source/delete", { sourceId });
  mayHaveImported = false;
  const statsAfterDelete = await request("/api/stats");
  assert(!statsAfterDelete.sources.some((source) => source.sourceName === sourceName), "source deletion did not refresh seekdb.");

  console.log("PASS: seekdb health, import, hybrid retrieval, incremental replacement, index refresh, deletion, and stats.");
} finally {
  if (mayHaveImported) await request("/api/source/delete", { sourceId });
}
