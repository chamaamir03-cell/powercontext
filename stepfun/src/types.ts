export interface RuntimeInfo {
  backend: "webgpu" | "wasm";
  dtype: "fp32" | "fp16" | "q8";
  fallbackReason?: string;
}
