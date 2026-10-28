import { EMBEDDING_DIMENSION, type BenchmarkResponse, type SearchResponse, type SourceSummary, type StatsResponse } from "../shared/contracts";
import type { BenchmarkRequest, IngestRequest } from "../shared/contracts";
import { EmbeddingClient, type BackendPreference, type EmbeddingProgress, type ModelProfile } from "./embedding-client";
import type { RuntimeInfo } from "./types";
import { calculateRetrievalMetrics } from "./lib/metrics";
import { calculateDistribution, type Distribution } from "./lib/statistics";
import { chunkText } from "./lib/chunking";
import "./styles.css";

const MODEL_ID = "Xenova/bge-small-zh-v1.5";
const MODEL_VERSION = "Xenova/bge-small-zh-v1.5 (BGE small Chinese v1.5)";
const QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章：";
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const TOP_K = 3;
const WARM_BENCHMARK_ROUNDS = 5;
const SIMULATE_WEBGPU_UNAVAILABLE = new URLSearchParams(location.search).get("acceptance-webgpu-unavailable") === "1";

const byId = <T extends HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing required page element: #${id}`);
  return element as T;
};

const fileInput = byId<HTMLInputElement>("file-input");
const dropZone = byId<HTMLDivElement>("drop-zone");
const selectedFilesElement = byId<HTMLDivElement>("selected-files");
const importButton = byId<HTMLButtonElement>("import-button");
const importProgress = byId<HTMLDivElement>("import-progress");
const modelProgress = byId<HTMLDivElement>("model-progress");
const backendChoice = byId<HTMLSelectElement>("backend-choice");
const runtimePill = byId<HTMLSpanElement>("runtime-pill");
const dbPill = byId<HTMLSpanElement>("db-pill");
const modelStatus = byId<HTMLElement>("model-status");
const backendStatus = byId<HTMLElement>("backend-status");
const documentCount = byId<HTMLElement>("document-count");
const sourceCount = byId<HTMLElement>("source-count");
const sourceList = byId<HTMLDivElement>("source-list");
const searchForm = byId<HTMLFormElement>("search-form");
const queryInput = byId<HTMLInputElement>("query-input");
const searchButton = byId<HTMLButtonElement>("search-button");
const searchStatus = byId<HTMLDivElement>("search-status");
const searchResults = byId<HTMLDivElement>("search-results");
const benchmarkButton = byId<HTMLButtonElement>("benchmark-button");
const benchmarkResults = byId<HTMLDivElement>("benchmark-results");
const operationMetrics = byId<HTMLDivElement>("operation-metrics");
const downloadButton = byId<HTMLButtonElement>("download-report");

interface IngestResponse {
  sourceId: string;
  sourceName: string;
  chunks: number;
  replacedChunks: number;
  removedChunks: number;
  writeRefreshMs: number;
}

interface DeleteResponse {
  deletedChunks: number;
}

interface BenchmarkCase {
  id: string;
  text: string;
}

interface BenchmarkQuestion {
  id: string;
  text: string;
  relevantIds: string[];
}

interface ProfileMeasurement {
  profile: ModelProfile;
  runtimeInfo: RuntimeInfo;
  modelBytes: number;
  coldRound: BenchmarkRound;
  warmRounds: BenchmarkRound[];
  summaries: Record<BenchmarkMetric, Distribution>;
  rounds: BenchmarkRound[];
}

type BenchmarkMetric =
  | "modelDownloadMs"
  | "modelInitializationMs"
  | "documentVectorizationMs"
  | "documentVectorsPerSecond"
  | "queryVectorizationMs"
  | "writeRefreshMs"
  | "databaseSearchMs"
  | "endToEndMs"
  | "recallAt3"
  | "ndcgAt3";

interface BenchmarkRound {
  round: number;
  phase: "cold" | "warm";
  modelBytes: number;
  modelDownloadMs: number;
  modelInitializationMs: number;
  documentVectorizationMs: number;
  documentVectorsPerSecond: number;
  queryVectorizationMs: number;
  writeRefreshMs: number;
  databaseSearchMs: number;
  endToEndMs: number;
  recallAt3: number;
  ndcgAt3: number;
}

interface MeasurementReport {
  measuredAt: string;
  browser: string;
  model: string;
  modelDimension: number;
  quantization: string;
  backendPreference: BackendPreference;
  device: {
    userAgent: string;
    hardwareConcurrency: number;
    webgpuAvailable: boolean;
    simulatedWebGPUUnavailable: boolean;
  };
  operation: string;
  metrics: Record<string, unknown>;
}

let selectedFiles: File[] = [];
let currentStats: StatsResponse = { documentCount: 0, sources: [] };
let latestReport: MeasurementReport | undefined;
let busy = false;
let embeddingClient = new EmbeddingClient();

function formatMs(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value < 1_000) return `${value.toFixed(1)} ms`;
  return `${(value / 1_000).toFixed(2)} s`;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "未报告";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatRate(value: number): string {
  return Number.isFinite(value) && value > 0 ? `${value.toFixed(1)} 段/秒` : "—";
}

function setBusy(next: boolean): void {
  busy = next;
  importButton.disabled = busy || selectedFiles.length === 0;
  searchButton.disabled = busy;
  benchmarkButton.disabled = busy;
  fileInput.disabled = busy;
  backendChoice.disabled = busy;
}

function updateRuntime(runtime: RuntimeInfo, modelBytes = 0): void {
  const backend = runtime.backend === "webgpu" ? "WebGPU" : "WASM";
  const quantization = runtime.dtype === "q8" ? "INT8 / Q8" : runtime.dtype.toUpperCase();
  runtimePill.className = "pill pill-success";
  runtimePill.innerHTML = `<i></i> ${backend} · ${quantization}`;
  modelStatus.textContent = `${quantization} 模型已就绪`;
  backendStatus.textContent = `${backend}${runtime.fallbackReason ? " · 已自动降级" : ""}`;
  if (runtime.fallbackReason) {
    modelProgress.textContent = `WebGPU 不可用，已降级至 WASM：${runtime.fallbackReason}`;
  }
  if (modelBytes > 0) {
    modelProgress.textContent = `最大模型权重文件已加载 · ${formatBytes(modelBytes)}`;
  }
}

function setModelProgress(progress: EmbeddingProgress, modelBytes: number): void {
  if (progress.status === "fallback") {
    modelProgress.textContent = "WebGPU 初始化未成功，正在自动切换 WASM…";
    return;
  }
  const file = progress.file?.split("/").at(-1);
  if (progress.total && progress.total > 0) {
    const percent = progress.loaded ? Math.min(100, (progress.loaded / progress.total) * 100) : progress.progress;
    modelProgress.textContent = `${file ?? "模型文件"} · ${percent ? `${percent.toFixed(0)}%` : formatBytes(modelBytes || progress.total)}`;
  } else if (progress.status) {
    modelProgress.textContent = `模型加载：${progress.status}${file ? ` · ${file}` : ""}`;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error(`本地 API 返回了非 JSON 响应（HTTP ${response.status}）。请确认服务从 stepfun 目录正常启动。`);
  }
  const result: unknown = await response.json();
  if (!response.ok) {
  const message = isRecord(result) && typeof result.error === "string" ? result.error : `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return result as T;
}

async function refreshStats(): Promise<void> {
  currentStats = await apiRequest<StatsResponse>("/api/stats");
  documentCount.textContent = currentStats.documentCount.toLocaleString();
  sourceCount.textContent = String(currentStats.sources.length);
  renderSources();
}

function renderSources(): void {
  sourceList.replaceChildren();
  if (currentStats.sources.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "导入文档后会显示在这里。";
    sourceList.append(empty);
    return;
  }

  for (const source of currentStats.sources) {
    const item = document.createElement("div");
    item.className = "source-item";
    const info = document.createElement("div");
    info.className = "source-info";
    const icon = document.createElement("span");
    icon.className = "file-icon";
    icon.textContent = source.sourceName.toLowerCase().endsWith(".csv") ? "▤" : "▧";
    const details = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = source.sourceName;
    name.title = source.sourceName;
    const chunks = document.createElement("small");
    chunks.textContent = `${source.chunks.toLocaleString()} 个文本块`;
    details.append(name, chunks);
    info.append(icon, details);

    const remove = document.createElement("button");
    remove.className = "icon-button";
    remove.type = "button";
    remove.title = `从 seekdb 删除 ${source.sourceName}`;
    remove.setAttribute("aria-label", `删除 ${source.sourceName}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => void deleteSource(source));
    item.append(info, remove);
    sourceList.append(item);
  }
}

function updateSelectedFiles(files: File[]): void {
  selectedFiles = files;
  fileInput.value = "";
  selectedFilesElement.replaceChildren();
  for (const file of files) {
    const tag = document.createElement("span");
    tag.className = "file-tag";
    tag.textContent = `${file.name} · ${formatBytes(file.size)}`;
    selectedFilesElement.append(tag);
  }
  importButton.disabled = busy || files.length === 0;
}

function setOperationMetrics(items: Array<{ label: string; value: string; detail: string }>): void {
  operationMetrics.replaceChildren();
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "metric-card";
    const label = document.createElement("span");
    label.textContent = item.label;
    const value = document.createElement("strong");
    value.textContent = item.value;
    const detail = document.createElement("small");
    detail.textContent = item.detail;
    card.append(label, value, detail);
    operationMetrics.append(card);
  }
  downloadButton.disabled = !latestReport;
}

function saveReport(report: MeasurementReport): void {
  latestReport = report;
  downloadButton.disabled = false;
}

function makeReport(
  operation: string,
  backendPreference: BackendPreference,
  metrics: Record<string, unknown>,
): MeasurementReport {
  return {
    measuredAt: new Date().toISOString(),
    browser: navigator.userAgent,
    model: MODEL_VERSION,
    modelDimension: EMBEDDING_DIMENSION,
    quantization: "WebGPU FP16 / WASM INT8 (Q8); FP32 only used as evaluation baseline",
    backendPreference,
    device: {
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency || 0,
      webgpuAvailable: "gpu" in navigator,
      simulatedWebGPUUnavailable: SIMULATE_WEBGPU_UNAVAILABLE,
    },
    operation,
    metrics,
  };
}

function showApiHealth(ready: boolean): void {
  dbPill.className = ready ? "pill pill-success" : "pill pill-error";
  dbPill.innerHTML = `<i></i> ${ready ? "seekdb 已连接" : "seekdb 不可用"}`;
}

async function initializeApp(): Promise<void> {
  if (SIMULATE_WEBGPU_UNAVAILABLE) {
    modelProgress.textContent = "验收模式：将 WebGPU 视为不可用，检查自动降级到 WASM。";
  }
  try {
    const health = await apiRequest<{ status: string }>("/api/health");
    showApiHealth(health.status === "ready");
    await refreshStats();
  } catch (error) {
    showApiHealth(false);
    searchStatus.textContent = `无法连接本地 seekdb：${error instanceof Error ? error.message : String(error)}`;
    searchStatus.className = "inline-status error-text";
  }
}

function selectedBackend(): BackendPreference {
  return backendChoice.value === "wasm" ? "wasm" : "auto";
}

async function importSelectedFiles(): Promise<void> {
  if (selectedFiles.length === 0 || busy) return;
  setBusy(true);
  importProgress.replaceChildren();
  const perFile: Array<Record<string, unknown>> = [];
  let successCount = 0;

  try {
    const backendPreference = selectedBackend();
    for (const file of selectedFiles) {
      const line = document.createElement("p");
      line.className = "progress-line";
      line.textContent = `读取 ${file.name}…`;
      importProgress.append(line);
      if (file.size > MAX_FILE_BYTES) {
        line.className = "progress-line error-text";
        line.textContent = `${file.name} 超过 5 MB，已跳过。`;
        continue;
      }

      const text = await file.text();
      const chunks = chunkText(text);
      if (chunks.length === 0) {
        line.className = "progress-line error-text";
        line.textContent = `${file.name} 没有可导入的文本，已跳过。`;
        continue;
      }
      if (chunks.length > 1_000) {
        line.className = "progress-line error-text";
        line.textContent = `${file.name} 拆分为 ${chunks.length} 段，超过每个文件 1,000 段的限制。`;
        continue;
      }

      line.textContent = `${file.name}：正在本地计算 ${chunks.length} 段向量…`;
      const embeddingResult = await embeddingClient.embed(
        chunks.map((chunk) => chunk.text),
        {
          profile: "quantized",
          backendPreference,
          simulateWebGPUUnavailable: SIMULATE_WEBGPU_UNAVAILABLE,
          onProgress: setModelProgress,
          onBatch: (done, total) => {
            line.textContent = `${file.name}：本地向量化 ${done}/${total} 段…`;
          },
        },
      );
      updateRuntime(embeddingResult.runtimeInfo, embeddingResult.modelBytes);
      const vectorizationMs = Math.max(
        0,
        embeddingResult.elapsedMs - embeddingResult.modelDownloadMs - embeddingResult.modelInitializationMs,
      );

      const payload: IngestRequest = {
        sourceName: file.name,
        chunks: chunks.map((chunk, index) => ({
          text: chunk.text,
          index: chunk.index,
          embedding: embeddingResult.vectors[index],
        })),
      };
      line.textContent = `${file.name}：正在写入 seekdb 并刷新索引…`;
      const databaseStartedAt = performance.now();
      const ingested = await apiRequest<IngestResponse>("/api/ingest", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const elapsedMs = performance.now() - databaseStartedAt;
      line.className = "progress-line success-text";
      line.textContent = `${file.name}：已写入 ${ingested.chunks} 段${ingested.replacedChunks ? `，更新并替换了原有 ${ingested.replacedChunks} 段` : ""}。`;
      successCount += 1;
      perFile.push({
        sourceName: file.name,
        chunkCount: chunks.length,
        modelDownloadMs: embeddingResult.modelDownloadMs,
        modelInitializationMs: embeddingResult.modelInitializationMs,
        embeddingMs: vectorizationMs,
        embeddingWallClockMs: embeddingResult.elapsedMs,
        embeddingThroughputChunksPerSecond: vectorizationMs > 0 ? chunks.length / (vectorizationMs / 1_000) : 0,
        databaseWriteAndRefreshMs: ingested.writeRefreshMs,
        apiElapsedMs: elapsedMs,
        runtimeInfo: embeddingResult.runtimeInfo,
        modelBytes: embeddingResult.modelBytes,
      });

      setOperationMetrics([
        { label: "模型下载", value: formatMs(embeddingResult.modelDownloadMs), detail: "仅统计模型权重网络下载" },
        { label: "模型初始化", value: formatMs(embeddingResult.modelInitializationMs), detail: "不含网络下载时间" },
        { label: "向量化", value: formatMs(vectorizationMs), detail: formatRate(vectorizationMs > 0 ? chunks.length / (vectorizationMs / 1_000) : 0) },
        { label: "seekdb 写入", value: formatMs(ingested.writeRefreshMs), detail: "SDK upsert + 索引刷新" },
      ]);
    }

    await refreshStats();
    if (successCount > 0) {
      importProgress.insertAdjacentHTML("afterbegin", `<p class="success-summary">已完成 ${successCount} 个文件的本地向量化与 seekdb 写入。</p>`);
      saveReport(makeReport("document_import", backendPreference, {
        files: perFile,
        successfulFiles: successCount,
        totalChunks: perFile.reduce((sum, item) => sum + Number(item.chunkCount ?? 0), 0),
      }));
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const line = document.createElement("p");
    line.className = "progress-line error-text";
    line.textContent = `导入失败：${message}`;
    importProgress.append(line);
  } finally {
    updateSelectedFiles([]);
    setBusy(false);
  }
}

async function deleteSource(source: SourceSummary): Promise<void> {
  if (busy || !window.confirm(`从 seekdb 中删除「${source.sourceName}」的 ${source.chunks} 个文本块？`)) return;
  setBusy(true);
  try {
    const result = await apiRequest<DeleteResponse>("/api/source/delete", {
      method: "POST",
      body: JSON.stringify({ sourceId: source.sourceId }),
    });
    await refreshStats();
    searchStatus.textContent = `已从 seekdb 删除 ${result.deletedChunks} 个文本块并刷新索引。`;
    searchStatus.className = "inline-status success-text";
  } catch (error) {
    searchStatus.textContent = `删除失败：${error instanceof Error ? error.message : String(error)}`;
    searchStatus.className = "inline-status error-text";
  } finally {
    setBusy(false);
  }
}

function renderSearchResults(response: SearchResponse): void {
  searchResults.replaceChildren();
  if (response.hits.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-results";
    empty.textContent = "seekdb 没有返回匹配结果。可尝试更换关键词或先导入文档。";
    searchResults.append(empty);
    return;
  }

  response.hits.forEach((hit, index) => {
    const article = document.createElement("article");
    article.className = "result-card";
    const top = document.createElement("div");
    top.className = "result-meta";
    const rank = document.createElement("span");
    rank.className = "result-rank";
    rank.textContent = `0${index + 1}`;
    const source = document.createElement("span");
    source.className = "result-source";
    source.textContent = `${hit.sourceName} · 段落 ${hit.chunkIndex + 1}`;
    top.append(rank, source);
    const text = document.createElement("p");
    text.textContent = hit.text;
    article.append(top, text);
    searchResults.append(article);
  });
}

async function searchKnowledge(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query || busy) return;
  setBusy(true);
  searchStatus.textContent = "在浏览器本地生成查询向量…";
  searchStatus.className = "inline-status";
  searchResults.replaceChildren();
  const startedAt = performance.now();

  try {
    const backendPreference = selectedBackend();
    const embedding = await embeddingClient.embed([`${QUERY_INSTRUCTION}${query}`], {
      profile: "quantized",
      backendPreference,
      simulateWebGPUUnavailable: SIMULATE_WEBGPU_UNAVAILABLE,
      onProgress: setModelProgress,
    });
    updateRuntime(embedding.runtimeInfo, embedding.modelBytes);
    const queryVectorizationMs = Math.max(
    0,
    embedding.elapsedMs - embedding.modelDownloadMs - embedding.modelInitializationMs,
    );
    searchStatus.textContent = "seekdb 正在执行向量与全文混合检索…";
    const search = await apiRequest<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify({
        query,
        embedding: embedding.vectors[0],
        topK: Number(byId<HTMLSelectElement>("top-k").value),
      }),
    });
    const endToEndMs = performance.now() - startedAt;
    renderSearchResults(search);
    searchStatus.textContent = `${search.hits.length} 条结果 · seekdb 混合检索 ${formatMs(search.databaseMs)} · 端到端 ${formatMs(endToEndMs)}`;
    searchStatus.className = "inline-status success-text";
    setOperationMetrics([
      { label: "模型下载", value: formatMs(embedding.modelDownloadMs), detail: "仅统计模型权重网络下载" },
      { label: "模型初始化", value: formatMs(embedding.modelInitializationMs), detail: "不含网络下载时间" },
      { label: "查询向量", value: formatMs(queryVectorizationMs), detail: `查询文本 · ${embedding.runtimeInfo.dtype.toUpperCase()}` },
      { label: "seekdb 检索", value: formatMs(search.databaseMs), detail: "全文 + 向量 + RRF" },
      { label: "端到端查询", value: formatMs(endToEndMs), detail: "本地向量化 + seekdb 检索" },
    ]);
    saveReport(makeReport("hybrid_search", backendPreference, {
      query,
      topK: Number(byId<HTMLSelectElement>("top-k").value),
      queryEmbeddingMs: queryVectorizationMs,
      queryEmbeddingWallClockMs: embedding.elapsedMs,
      modelDownloadMs: embedding.modelDownloadMs,
      modelInitializationMs: embedding.modelInitializationMs,
      databaseHybridSearchMs: search.databaseMs,
      endToEndMs,
      returnedResults: search.hits.length,
      runtimeInfo: embedding.runtimeInfo,
      modelBytes: embedding.modelBytes,
    }));
  } catch (error) {
    searchStatus.textContent = `检索失败：${error instanceof Error ? error.message : String(error)}`;
    searchStatus.className = "inline-status error-text";
  } finally {
    setBusy(false);
  }
}

const benchmarkDocuments: BenchmarkCase[] = [
  { id: "rrf-hybrid", text: "seekdb 混合检索同时执行向量相似度与全文检索，再用 RRF 倒数排名融合候选结果。" },
  { id: "webgpu-fallback", text: "浏览器端使用 Transformers.js 和 WebGPU 推理；WebGPU 不可用时应自动降级到 WASM。" },
  { id: "private-inference", text: "Embedding 应在用户设备本地运行，文档内容不发送到云端推理 API。" },
  { id: "int8-quantization", text: "INT8 量化可以缩小模型文件体积并降低内存占用，需同时评估向量检索质量。" },
  { id: "batch-throughput", text: "将多个文本组成 batch 执行 embedding 可以减少推理调用开销并提升吞吐量。" },
  { id: "index-refresh", text: "文档写入 seekdb 后刷新向量索引，确保刚导入的内容可以立即被混合检索到。" },
];

const benchmarkQuestions: BenchmarkQuestion[] = [
  { id: "q-rrf", text: "seekdb 如何融合全文检索与向量相似度结果？", relevantIds: ["rrf-hybrid"] },
  { id: "q-fallback", text: "WebGPU 不支持时浏览器模型怎么运行？", relevantIds: ["webgpu-fallback"] },
  { id: "q-private", text: "怎么保证文档向量化不调用云端推理服务？", relevantIds: ["private-inference"] },
  { id: "q-int8", text: "INT8 量化会带来哪些模型体积和检索质量变化？", relevantIds: ["int8-quantization"] },
  { id: "q-batch", text: "怎样提高批量文档向量化的吞吐速度？", relevantIds: ["batch-throughput"] },
  { id: "q-refresh", text: "怎样让刚写入的 seekdb 文档立即可搜索？", relevantIds: ["index-refresh"] },
];

interface ProfileResult {
  measurement: ProfileMeasurement;
}

const benchmarkMetrics: BenchmarkMetric[] = [
  "modelDownloadMs",
  "modelInitializationMs",
  "documentVectorizationMs",
  "documentVectorsPerSecond",
  "queryVectorizationMs",
  "writeRefreshMs",
  "databaseSearchMs",
  "endToEndMs",
  "recallAt3",
  "ndcgAt3",
];

async function runProfile(
  profile: ModelProfile,
  preference: BackendPreference,
  progress: HTMLElement,
): Promise<ProfileResult> {
  const client = new EmbeddingClient();
  try {
    const rounds: BenchmarkRound[] = [];
    let runtimeInfo: RuntimeInfo | undefined;

    for (let roundIndex = 0; roundIndex <= WARM_BENCHMARK_ROUNDS; roundIndex += 1) {
      const phase = roundIndex === 0 ? "cold" : "warm";
      progress.textContent = `${profile === "fp32" ? "FP32" : "量化"}：${phase === "cold" ? "首次" : `预热后第 ${roundIndex} 轮`}评测（共 ${WARM_BENCHMARK_ROUNDS} 轮预热测量）…`;

      const documentEmbedding = await client.embed(
        benchmarkDocuments.map((item) => item.text),
        {
          profile,
          backendPreference: preference,
          simulateWebGPUUnavailable: SIMULATE_WEBGPU_UNAVAILABLE,
          onProgress: setModelProgress,
        },
      );
      updateRuntime(documentEmbedding.runtimeInfo, documentEmbedding.modelBytes);

      const queryEmbedding = await client.embed(
        benchmarkQuestions.map((item) => `${QUERY_INSTRUCTION}${item.text}`),
        {
          profile,
          backendPreference: preference,
          simulateWebGPUUnavailable: SIMULATE_WEBGPU_UNAVAILABLE,
          onProgress: setModelProgress,
        },
      );
      if (documentEmbedding.runtimeInfo.backend !== queryEmbedding.runtimeInfo.backend) {
        if (preference === "auto") {
          client.close();
          return await runProfile(profile, "wasm", progress);
        }
        throw new Error("FP32 / 量化评测的文档与查询使用了不同推理后端。");
      }
      runtimeInfo = documentEmbedding.runtimeInfo;

      const payload: BenchmarkRequest = {
        profile,
        topK: TOP_K,
        documents: benchmarkDocuments.map((item, index) => ({
          ...item,
          embedding: documentEmbedding.vectors[index],
        })),
        queries: benchmarkQuestions.map((item, index) => ({
          ...item,
          embedding: queryEmbedding.vectors[index],
        })),
      };
      const result = await apiRequest<BenchmarkResponse>("/api/benchmark", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const quality = calculateRetrievalMetrics(
        benchmarkQuestions.map(({ id, relevantIds }) => ({ id, relevantIds })),
        result.searches.map(({ queryId, ids }) => ({ queryId, ids })),
        TOP_K,
      );
      const searchMs = result.searches.reduce((sum, item) => sum + item.databaseMs, 0);
      const documentVectorizationMs = Math.max(
        0,
        documentEmbedding.elapsedMs
          - documentEmbedding.modelDownloadMs
          - documentEmbedding.modelInitializationMs,
      );
      const queryVectorizationMs = Math.max(
        0,
        queryEmbedding.elapsedMs
          - queryEmbedding.modelDownloadMs
          - queryEmbedding.modelInitializationMs,
      );
      const endToEndMs = documentEmbedding.elapsedMs
        + queryEmbedding.elapsedMs
        + result.writeRefreshMs
        + searchMs;

      rounds.push({
        round: roundIndex + 1,
        phase,
        modelBytes: Math.max(documentEmbedding.modelBytes, queryEmbedding.modelBytes),
        modelDownloadMs: documentEmbedding.modelDownloadMs + queryEmbedding.modelDownloadMs,
        modelInitializationMs: documentEmbedding.modelInitializationMs + queryEmbedding.modelInitializationMs,
        documentVectorizationMs,
        documentVectorsPerSecond: documentVectorizationMs > 0
          ? benchmarkDocuments.length / (documentVectorizationMs / 1_000)
          : 0,
        queryVectorizationMs,
        writeRefreshMs: result.writeRefreshMs,
        databaseSearchMs: searchMs / Math.max(1, result.searches.length),
        endToEndMs,
        recallAt3: quality.recallAtK,
        ndcgAt3: quality.ndcgAtK,
      });
    }

    if (!runtimeInfo) throw new Error("Embedding runtime did not report the selected backend.");
    const warmRounds = rounds.slice(1);
    const summaries = Object.fromEntries(
      benchmarkMetrics.map((metric) => [
        metric,
        calculateDistribution(warmRounds.map((round) => round[metric])),
      ]),
    ) as Record<BenchmarkMetric, Distribution>;
    return {
      measurement: {
        profile,
        runtimeInfo,
        modelBytes: rounds[0].modelBytes,
        coldRound: rounds[0],
        warmRounds,
        summaries,
        rounds,
      },
    };
  } finally {
    client.close();
  }
}

function renderBenchmark(measurements: ProfileMeasurement[]): void {
  benchmarkResults.replaceChildren();
  const heading = document.createElement("p");
  heading.className = "benchmark-complete";
  heading.textContent = `固定评测集完成 · 6 篇文档 / 6 个查询 · Top-3 · 每配置 1 次首次 + ${WARM_BENCHMARK_ROUNDS} 次预热测量`;
  benchmarkResults.append(heading);

  const table = document.createElement("div");
  table.className = "benchmark-table";
  const warmMetric = (
    item: ProfileMeasurement,
    metric: BenchmarkMetric,
    format: (value: number) => string,
  ): string => {
    const distribution = item.summaries[metric];
    return `中位数 ${format(distribution.median)} / P95 ${format(distribution.p95)}`;
  };
  const rows: Array<[string, (measurement: ProfileMeasurement) => string]> = [
    ["配置 / 后端", (item) => `${item.profile === "fp32" ? "FP32 基线" : "量化"} · ${item.runtimeInfo.backend.toUpperCase()} ${item.runtimeInfo.dtype.toUpperCase()}`],
    ["模型文件体积", (item) => formatBytes(item.modelBytes)],
    ["模型下载（首次轮）", (item) => formatMs(item.coldRound.modelDownloadMs)],
    ["模型初始化（首次轮，不含下载）", (item) => formatMs(item.coldRound.modelInitializationMs)],
    ["模型初始化（预热）", (item) => warmMetric(item, "modelInitializationMs", formatMs)],
    ["文档向量化（预热）", (item) => warmMetric(item, "documentVectorizationMs", formatMs)],
    ["文档吞吐（预热）", (item) => warmMetric(item, "documentVectorsPerSecond", (value) => formatRate(value))],
    ["查询向量化（预热）", (item) => warmMetric(item, "queryVectorizationMs", formatMs)],
    ["seekdb 写入 + 刷新（预热）", (item) => warmMetric(item, "writeRefreshMs", formatMs)],
    ["seekdb 混合检索（预热，单查询）", (item) => warmMetric(item, "databaseSearchMs", formatMs)],
    ["Recall@3（预热中位数）", (item) => `${(item.summaries.recallAt3.median * 100).toFixed(1)}%`],
    ["nDCG@3（预热中位数）", (item) => item.summaries.ndcgAt3.median.toFixed(3)],
    ["端到端（预热）", (item) => warmMetric(item, "endToEndMs", formatMs)],
  ];

  const header = document.createElement("div");
  header.className = "benchmark-row benchmark-header";
  const metricHeader = document.createElement("span");
  metricHeader.textContent = "测量项";
  header.append(metricHeader);
  for (const item of measurements) {
    const cell = document.createElement("span");
    cell.textContent = item.profile === "fp32" ? "FP32 基线" : "量化后";
    header.append(cell);
  }
  table.append(header);
  for (const [label, valueFor] of rows) {
    const row = document.createElement("div");
    row.className = "benchmark-row";
    const name = document.createElement("span");
    name.textContent = label;
    row.append(name);
    for (const item of measurements) {
      const value = document.createElement("span");
      value.textContent = valueFor(item);
      row.append(value);
    }
    table.append(row);
  }
  benchmarkResults.append(table);

  const fp32 = measurements.find((item) => item.profile === "fp32");
  const quantized = measurements.find((item) => item.profile === "quantized");
  if (fp32 && quantized) {
    const compression = document.createElement("p");
    const ratio = fp32.modelBytes > 0 && quantized.modelBytes > 0
      ? `${(fp32.modelBytes / quantized.modelBytes).toFixed(2)}× 体积压缩`
      : "模型文件总大小未能从权重文件读取";
    compression.className = "compression-note";
    compression.textContent = `${ratio} · Recall@3 变化 ${((quantized.summaries.recallAt3.median - fp32.summaries.recallAt3.median) * 100).toFixed(1)} 个百分点 · nDCG@3 变化 ${(quantized.summaries.ndcgAt3.median - fp32.summaries.ndcgAt3.median).toFixed(3)}`;
    benchmarkResults.append(compression);
  }
}

async function runBenchmark(): Promise<void> {
  if (busy) return;
  setBusy(true);
  benchmarkResults.replaceChildren();
  const progress = document.createElement("p");
  progress.className = "inline-status";
  progress.textContent = "加载量化模型并确认本次评测的实际后端…";
  benchmarkResults.append(progress);
  const preference = selectedBackend();

  try {
    let quantized = await runProfile("quantized", preference, progress);
    const baselinePreference: BackendPreference = quantized.measurement.runtimeInfo.backend === "wasm" ? "wasm" : "auto";
    progress.textContent = "量化配置完成；正在使用同一推理后端运行 FP32 基线…";
    let fp32 = await runProfile("fp32", baselinePreference, progress);
    if (fp32.measurement.runtimeInfo.backend !== quantized.measurement.runtimeInfo.backend) {
      progress.textContent = "FP32 模型不支持相同的 WebGPU 路径；正在将两组评测统一切换到 WASM…";
      quantized = await runProfile("quantized", "wasm", progress);
      fp32 = await runProfile("fp32", "wasm", progress);
    }
    if (fp32.measurement.runtimeInfo.backend !== quantized.measurement.runtimeInfo.backend) {
      throw new Error("FP32 与量化模型无法在相同后端完成评测，结果未报告。");
    }
    const measurements = [fp32.measurement, quantized.measurement];
    renderBenchmark(measurements);
    const report = makeReport("fixed_retrieval_quality_and_performance_benchmark", preference, {
      model: MODEL_ID,
      quantizationComparison: measurements,
      compressionRatio: fp32.measurement.modelBytes > 0 && quantized.measurement.modelBytes > 0
        ? fp32.measurement.modelBytes / quantized.measurement.modelBytes
        : null,
      qualitySet: benchmarkQuestions.map(({ id, relevantIds }) => ({ id, relevantIds })),
      measurementConditions: {
        evaluationDocuments: benchmarkDocuments.length,
        queries: benchmarkQuestions.length,
        topK: TOP_K,
        coldRounds: 1,
        warmRounds: WARM_BENCHMARK_ROUNDS,
        aggregation: "median and nearest-rank P95 across warm rounds; all raw cold and warm rounds are included",
        browser: navigator.userAgent,
        webgpuAvailable: "gpu" in navigator,
        simulatedWebGPUUnavailable: SIMULATE_WEBGPU_UNAVAILABLE,
        hardwareConcurrency: navigator.hardwareConcurrency || 0,
        backendPreference: preference,
        note: "Model bytes sum unique ONNX weight file sizes observed by Transformers.js. Download time is measured only for ONNX weight fetches; cached weights report zero network download. The first round is the first run in this benchmark, not necessarily a cold browser-cache run.",
      },
    });
    saveReport(report);
    setOperationMetrics([
      { label: "模型下载", value: `${formatMs(fp32.measurement.coldRound.modelDownloadMs)} / ${formatMs(quantized.measurement.coldRound.modelDownloadMs)}`, detail: "首次轮 · FP32 / 量化" },
      { label: "模型初始化", value: `${formatMs(fp32.measurement.coldRound.modelInitializationMs)} / ${formatMs(quantized.measurement.coldRound.modelInitializationMs)}`, detail: "首次轮，不含下载 · FP32 / 量化" },
      {
        label: "文档向量化 P50/P95",
        value: `${formatMs(fp32.measurement.summaries.documentVectorizationMs.median)} / ${formatMs(fp32.measurement.summaries.documentVectorizationMs.p95)}`,
        detail: `FP32；量化 ${formatMs(quantized.measurement.summaries.documentVectorizationMs.median)} / ${formatMs(quantized.measurement.summaries.documentVectorizationMs.p95)}`,
      },
      {
        label: "seekdb 写入 P50/P95",
        value: `${formatMs(fp32.measurement.summaries.writeRefreshMs.median)} / ${formatMs(fp32.measurement.summaries.writeRefreshMs.p95)}`,
        detail: `FP32；量化 ${formatMs(quantized.measurement.summaries.writeRefreshMs.median)} / ${formatMs(quantized.measurement.summaries.writeRefreshMs.p95)}`,
      },
      { label: "Recall@3", value: `${(fp32.measurement.summaries.recallAt3.median * 100).toFixed(0)}% → ${(quantized.measurement.summaries.recallAt3.median * 100).toFixed(0)}%`, detail: "FP32 → 量化 · seekdb 混合检索" },
    ]);
  } catch (error) {
    progress.className = "inline-status error-text";
    progress.textContent = `评测失败：${error instanceof Error ? error.message : String(error)}`;
  } finally {
    setBusy(false);
  }
}

fileInput.addEventListener("change", () => updateSelectedFiles(Array.from(fileInput.files ?? [])));
dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("drag-over");
  if (busy) return;
  updateSelectedFiles(Array.from(event.dataTransfer?.files ?? []));
});
importButton.addEventListener("click", () => void importSelectedFiles());
searchForm.addEventListener("submit", (event) => void searchKnowledge(event));
benchmarkButton.addEventListener("click", () => void runBenchmark());
downloadButton.addEventListener("click", () => {
  if (!latestReport) return;
  const blob = new Blob([JSON.stringify(latestReport, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `stepfun-measurement-${latestReport.operation}-${Date.now()}.json`;
  link.click();
  URL.revokeObjectURL(url);
});
window.addEventListener("pagehide", () => embeddingClient.close());
backendChoice.addEventListener("change", () => {
  embeddingClient.close();
  embeddingClient = new EmbeddingClient();
  modelProgress.textContent = "推理后端偏好已更新。";
});

void initializeApp();
