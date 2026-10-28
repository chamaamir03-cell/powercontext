export interface RankedResult {
  queryId: string;
  ids: string[];
}

export interface RelevanceSet {
  id: string;
  relevantIds: string[];
}

export interface RetrievalMetrics {
  recallAtK: number;
  ndcgAtK: number;
  k: number;
}

export function calculateRetrievalMetrics(
  expected: RelevanceSet[],
  actual: RankedResult[],
  k: number,
): RetrievalMetrics {
  if (k < 1) throw new RangeError("k must be at least 1");
  if (expected.length === 0) throw new RangeError("expected must not be empty");

  const rankedByQuery = new Map(actual.map((result) => [result.queryId, result.ids]));
  let recallTotal = 0;
  let ndcgTotal = 0;

  for (const item of expected) {
    const relevant = new Set(item.relevantIds);
    const ranked = rankedByQuery.get(item.id) ?? [];
    const top = ranked.slice(0, k);
    const found = top.filter((id) => relevant.has(id)).length;
    recallTotal += relevant.size === 0 ? 1 : found / relevant.size;

    const dcg = top.reduce(
      (score, id, rank) => score + (relevant.has(id) ? 1 / Math.log2(rank + 2) : 0),
      0,
    );
    const idealHits = Math.min(relevant.size, k);
    const idcg = Array.from({ length: idealHits }, (_, rank) => 1 / Math.log2(rank + 2))
      .reduce((sum, score) => sum + score, 0);
    ndcgTotal += idcg === 0 ? 1 : dcg / idcg;
  }

  return {
    recallAtK: recallTotal / expected.length,
    ndcgAtK: ndcgTotal / expected.length,
    k,
  };
}
