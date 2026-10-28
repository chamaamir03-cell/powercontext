import { env, pipeline, type FeatureExtractionPipeline } from "@huggingface/transformers";

type ModelProfile = "fp32" | "quantized";
type BackendPreference = "auto" | "wasm";

interface EmbedRequest {
  id: number;
  type: "embed";
  texts: string[];
  profile: ModelProfile;
  backendPreference: BackendPreference;
  simulateWebGPUUnavailable?: boolean;
}

interface WorkerProgress {
  status?: string;
  file?: string;
  loaded?: number;
  total?: number;
  progress?: number;
}

interface RuntimeInfo {
  backend: "webgpu" | "wasm";
  dtype: "fp32" | "fp16" | "q8";
  fallbackReason?: string;
}

interface WorkerScope {
  addEventListener(type: "message", listener: (event: MessageEvent<EmbedRequest>) => void): void;
  postMessage(message: unknown): void;
}

const workerScope = globalThis as unknown as WorkerScope;
const MODEL_ID = "Xenova/bge-small-zh-v1.5";

env.allowLocalModels = false;
env.useBrowserCache = true;
const wasmBackend = env.backends.onnx.wasm;
if (wasmBackend) {
  wasmBackend.numThreads = Math.max(1, Math.min(4, (navigator.hardwareConcurrency || 2) - 1));
}

let extractor: FeatureExtractionPipeline | undefined;
let runtimeInfo: RuntimeInfo | undefined;
let activeProfile: ModelProfile | undefined;
let activePreference: BackendPreference | undefined;
let modelBytes = 0;
let modelDownloadMs = 0;
let modelInitializationMs = 0;
const modelFileSizes = new Map<string, number>();

function sendProgress(id: number, progress: WorkerProgress): void {
  if (typeof progress.total === "number" && progress.file?.toLowerCase().endsWith(".onnx")) {
    modelFileSizes.set(progress.file, progress.total);
    modelBytes = [...modelFileSizes.values()].reduce((sum, size) => sum + size, 0);
  }
  workerScope.postMessage({ id, type: "progress", progress, modelBytes, runtimeInfo });
}

async function createExtractor(
  id: number,
  profile: ModelProfile,
  backend: "webgpu" | "wasm",
): Promise<FeatureExtractionPipeline> {
  const dtype: RuntimeInfo["dtype"] = profile === "fp32" ? "fp32" : backend === "webgpu" ? "fp16" : "q8";
  let downloadStartedAt: number | undefined;
  let downloadFinishedAt: number | undefined;
  try {
    const created = await pipeline("feature-extraction", MODEL_ID, {
      device: backend,
      dtype,
      progress_callback: (progress: WorkerProgress) => {
        if (progress.file?.toLowerCase().endsWith(".onnx")) {
          if (progress.status === "download" && downloadStartedAt === undefined) {
            downloadStartedAt = performance.now();
          } else if (progress.status === "done" && downloadStartedAt !== undefined) {
            downloadFinishedAt = performance.now();
          }
        }
        sendProgress(id, progress);
      },
    });
    if (downloadStartedAt !== undefined) {
      modelDownloadMs += Math.max(0, (downloadFinishedAt ?? performance.now()) - downloadStartedAt);
    }

    runtimeInfo = { backend, dtype };
    return created as FeatureExtractionPipeline;
  } catch (error) {
    if (downloadStartedAt !== undefined) {
      modelDownloadMs += Math.max(0, (downloadFinishedAt ?? performance.now()) - downloadStartedAt);
    }
    throw error;
  }
}

async function initialize(
  id: number,
  profile: ModelProfile,
  preference: BackendPreference,
  simulateWebGPUUnavailable: boolean,
): Promise<FeatureExtractionPipeline> {
  if (extractor && activeProfile === profile && activePreference === preference) {
    modelDownloadMs = 0;
    modelInitializationMs = 0;
    return extractor;
  }

  extractor = undefined;
  runtimeInfo = undefined;
  modelBytes = 0;
  modelFileSizes.clear();
  modelDownloadMs = 0;
  modelInitializationMs = 0;
  let fallbackReason: string | undefined;
  const initializationStartedAt = performance.now();

  if (preference === "auto" && !simulateWebGPUUnavailable && "gpu" in navigator) {
    try {
      extractor = await createExtractor(id, profile, "webgpu");
      modelInitializationMs = Math.max(0, performance.now() - initializationStartedAt - modelDownloadMs);
      activeProfile = profile;
      activePreference = preference;
      return extractor;
    } catch (error) {
      fallbackReason = error instanceof Error ? error.message : String(error);
      sendProgress(id, {
        status: "fallback",
        progress: 0,
      });
    }
  } else if (preference === "auto") {
    fallbackReason = simulateWebGPUUnavailable
      ? "WebGPU was marked unavailable for acceptance testing."
      : "WebGPU is not available in this browser.";
  }

  extractor = await createExtractor(id, profile, "wasm");
  modelInitializationMs = Math.max(0, performance.now() - initializationStartedAt - modelDownloadMs);
  if (fallbackReason) {
    runtimeInfo = {
      backend: "wasm",
      dtype: profile === "fp32" ? "fp32" : "q8",
      fallbackReason,
    };
  }
  activeProfile = profile;
  activePreference = preference;
  return extractor;
}

function toVectors(output: Awaited<ReturnType<FeatureExtractionPipeline>>): number[][] {
  const values = output.tolist();
  if (!Array.isArray(values) || !Array.isArray(values[0])) {
    throw new Error("Embedding model returned an unexpected tensor shape.");
  }
  return values as number[][];
}

workerScope.addEventListener("message", async (event: MessageEvent<EmbedRequest>) => {
  const request = event.data;
  if (request.type !== "embed") return;
  try {
    const pipelineInstance = await initialize(
      request.id,
      request.profile,
      request.backendPreference,
      request.simulateWebGPUUnavailable ?? false,
    );
    let vectors: number[][];
    try {
      const output = await pipelineInstance(request.texts, {
        pooling: "mean",
        normalize: true,
      });
      vectors = toVectors(output);
    } catch (error) {
      if (runtimeInfo?.backend !== "webgpu") throw error;

      const fallbackReason = error instanceof Error ? error.message : String(error);
      const previousInitializationMs = modelInitializationMs;
      const previousDownloadMs = modelDownloadMs;
      const loadStartedAt = performance.now();
      extractor = await createExtractor(request.id, request.profile, "wasm");
      modelInitializationMs = previousInitializationMs
        + Math.max(0, performance.now() - loadStartedAt - (modelDownloadMs - previousDownloadMs));
      activeProfile = request.profile;
      activePreference = request.backendPreference;
      runtimeInfo = {
        backend: "wasm",
        dtype: request.profile === "fp32" ? "fp32" : "q8",
        fallbackReason,
      };
      const output = await extractor(request.texts, {
        pooling: "mean",
        normalize: true,
      });
      vectors = toVectors(output);
    }

    workerScope.postMessage({
      id: request.id,
      type: "result",
      vectors,
      runtimeInfo,
      modelBytes,
      modelDownloadMs,
      modelInitializationMs,
    });
  } catch (error) {
    workerScope.postMessage({
      id: request.id,
      type: "error",
      error: error instanceof Error ? error.message : String(error),
    });
  }
});
