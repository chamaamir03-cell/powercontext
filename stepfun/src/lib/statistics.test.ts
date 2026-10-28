import { describe, expect, it } from "vitest";
import { calculateDistribution } from "./statistics";

describe("calculateDistribution", () => {
  it("calculates the median and nearest-rank p95 without mutating input", () => {
    const values = [8, 1, 4, 2, 16];
    expect(calculateDistribution(values)).toEqual({ median: 4, p95: 16 });
    expect(values).toEqual([8, 1, 4, 2, 16]);
  });

  it("averages the middle pair for even-sized samples", () => {
    expect(calculateDistribution([2, 4, 6, 8])).toEqual({ median: 5, p95: 8 });
  });

  it("rejects empty or non-finite measurements", () => {
    expect(() => calculateDistribution([])).toThrow(RangeError);
    expect(() => calculateDistribution([1, Number.NaN])).toThrow(RangeError);
  });
});
