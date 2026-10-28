import { describe, expect, it } from "vitest";
import { calculateRetrievalMetrics } from "./metrics";

describe("calculateRetrievalMetrics", () => {
  it("computes Recall@K and nDCG@K from ranked seekdb results", () => {
    const metrics = calculateRetrievalMetrics(
      [
        { id: "q1", relevantIds: ["a"] },
        { id: "q2", relevantIds: ["b", "c"] },
      ],
      [
        { queryId: "q1", ids: ["x", "a"] },
        { queryId: "q2", ids: ["b", "x", "c"] },
      ],
      3,
    );

    expect(metrics.recallAtK).toBe(1);
    expect(metrics.ndcgAtK).toBeCloseTo((1 / Math.log2(3) + (1 + 1 / Math.log2(4)) / (1 + 1 / Math.log2(3))) / 2);
  });

  it("rejects invalid cutoffs and empty evaluation sets", () => {
    expect(() => calculateRetrievalMetrics([], [], 3)).toThrow(RangeError);
    expect(() => calculateRetrievalMetrics([{ id: "q", relevantIds: [] }], [], 0)).toThrow(RangeError);
  });
});
