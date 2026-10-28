import type { RuntimeInfo } from "./types";

export type ModelProfile = "fp32" | "quantized";
export type BackendPreference = "auto" | "wasm";

export interface EmbeddingProgress {
  status?: string;
  file?: string;
  loaded?: number;
  total?: number;
  progress?: number;
}

export interface EmbeddingBatch {
  vectors: number[][];
  runtimeInfo: RuntimeInfo;
  modelBytes: number;
  modelDownloadMs: number;
  modelInitializationMs: number;
  elapsedMs: number;
}

interface WorkerMessage {
  id: number;
  type: "progress" | "result" | "error";
  progress?: EmbeddingProgress;
  runtimeInfo?: RuntimeInfo;
  modelBytes?: number;
  modelDownloadMs?: number;
  modelInitializationMs?: number;
  vectors?: number[][];
  error?: string;
}

interface PendingRequest {
  resolve: (result: EmbeddingBatch) => void;
  reject: (error: Error) => void;
  startedAt: number;
  onProgress?: (progress: EmbeddingProgress, modelBytes: number) => void;
}

const BATCH_SIZE = 16;

export class EmbeddingClient {
  private worker: Worker | undefined;
  private nextId = 0;
  private readonly pending = new Map<number, PendingRequest>();

  private getWorker(): Worker {
    if (this.worker) return this.worker;

    this.worker = new Worker(new URL("./embedding.worker.ts", import.meta.url), {
      type: "module",
    });
    this.worker.addEventListener("message", (event: MessageEvent<WorkerMessage>) => {
      const message = event.data;
      const pending = this.pending.get(message.id);
      if (!pending) return;

      if (message.type === "progress") {
        pending.onProgress?.(message.progress ?? {}, message.modelBytes ?? 0);
      } else if (message.type === "error") {
        this.pending.delete(message.id);
        pending.reject(new Error(message.error ?? "Embedding failed."));
      } else if (message.type === "result") {
        this.pending.delete(message.id);
        if (!message.vectors || !message.runtimeInfo) {
          pending.reject(new Error("Embedding worker returned an incomplete result."));
          return;
        }
        pending.resolve({
          vectors: message.vectors,
          runtimeInfo: message.runtimeInfo,
          modelBytes: message.modelBytes ?? 0,
          modelDownloadMs: message.modelDownloadMs ?? 0,
          modelInitializationMs: message.modelInitializationMs ?? 0,
          elapsedMs: performance.now() - pending.startedAt,
        });
      }
    });
    this.worker.addEventListener("error", (event) => {
      const error = new Error(event.message || "Embedding worker stopped unexpectedly.");
      for (const pending of this.pending.values()) pending.reject(error);
      this.pending.clear();
      this.close();
    });
    return this.worker;
  }

  async embed(
    texts: string[],
    options: {
      profile: ModelProfile;
      backendPreference: BackendPreference;
      simulateWebGPUUnavailable?: boolean;
      onProgress?: (progress: EmbeddingProgress, modelBytes: number) => void;
      onBatch?: (completed: number, total: number) => void;
    },
  ): Promise<EmbeddingBatch> {
    if (texts.length === 0) {
      return {
        vectors: [],
        runtimeInfo: { backend: "wasm", dtype: "q8" },
        modelBytes: 0,
        modelDownloadMs: 0,
        modelInitializationMs: 0,
        elapsedMs: 0,
      };
    }

    const allVectors: number[][] = [];
    let lastInfo: RuntimeInfo | undefined;
    let modelBytes = 0;
    let modelDownloadMs = 0;
    let modelInitializationMs = 0;
    const startedAt = performance.now();
    let offset = 0;

    while (offset < texts.length) {
      const batch = texts.slice(offset, offset + BATCH_SIZE);
      const id = this.nextId++;
      const worker = this.getWorker();
      const result = await new Promise<EmbeddingBatch>((resolve, reject) => {
        this.pending.set(id, {
          resolve,
          reject,
          startedAt: performance.now(),
          onProgress: options.onProgress,
        });
        worker.postMessage({
          id,
          type: "embed",
          texts: batch,
          profile: options.profile,
          backendPreference: options.backendPreference,
          simulateWebGPUUnavailable: options.simulateWebGPUUnavailable,
        });
      });
      if (result.vectors.length !== batch.length) {
        throw new Error("Embedding model returned a different number of vectors than input texts.");
      }
      modelBytes = Math.max(modelBytes, result.modelBytes);
      modelDownloadMs += result.modelDownloadMs;
      modelInitializationMs += result.modelInitializationMs;
      if (lastInfo?.backend === "webgpu" && result.runtimeInfo.backend === "wasm") {
        allVectors.length = 0;
        lastInfo = result.runtimeInfo;
        offset = 0;
        continue;
      }
      if (lastInfo && lastInfo.backend !== result.runtimeInfo.backend) {
        throw new Error("Embedding batches used different inference backends.");
      }
      allVectors.push(...result.vectors);
      lastInfo = result.runtimeInfo;
      options.onBatch?.(allVectors.length, texts.length);
      offset += batch.length;
    }

    if (!lastInfo) throw new Error("Embedding runtime did not report its selected backend.");
    return {
      vectors: allVectors,
      runtimeInfo: lastInfo,
      modelBytes,
      modelDownloadMs,
      modelInitializationMs,
      elapsedMs: performance.now() - startedAt,
    };
  }

  close(): void {
    this.worker?.terminate();
    this.worker = undefined;
    for (const pending of this.pending.values()) pending.reject(new Error("Embedding worker was closed."));
    this.pending.clear();
  }
}
