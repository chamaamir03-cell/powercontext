export interface Distribution {
  median: number;
  p95: number;
}

export function calculateDistribution(values: number[]): Distribution {
  if (values.length === 0 || values.some((value) => !Number.isFinite(value))) {
    throw new RangeError("values must contain finite measurements.");
  }

  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
  const p95Index = Math.ceil(sorted.length * 0.95) - 1;

  return { median, p95: sorted[p95Index] };
}
