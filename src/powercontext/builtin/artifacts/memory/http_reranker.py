# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP cross-encoder reranking for Memory search candidates."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import (
    MemoryRerankDecision,
    MemoryReranker,
    _validate_bounds,
)
from powercontext.builtin.inference import InferenceUsage

if TYPE_CHECKING:
    import httpx


class _InvalidRerankerArgumentError(ValueError):
    def __init__(self, parameter: str) -> None:
        messages = {
            "batch_size": "batch_size must be a positive integer",
            "limit": "limit must be an integer",
        }
        super().__init__(messages[parameter])


class _InvalidRerankerResponseError(ValueError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        messages = {
            "invalid-json": "reranker response must contain valid JSON",
            "top-level-fields": "reranker response must contain only the scores field",
            "scores-type": "reranker scores must be a JSON array",
            "entry-fields": "each reranker score must contain only rank and score",
            "rank-type": "reranker rank must be an integer",
            "duplicate-rank": "reranker scores must not contain duplicate ranks",
            "batch-rank": "reranker score rank does not belong to this batch",
            "score-type": "reranker score must be numeric",
            "score-finite": "reranker score must be finite",
            "rank-coverage": "reranker scores must cover each batch rank exactly once",
        }
        message = messages[code]
        if detail is not None:
            message = f"{message}: {detail}"
        super().__init__(message)


class HTTPMemoryReranker(MemoryReranker):
    """Score candidates through a caller-owned client; install the ``client`` extra to use it."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise _InvalidRerankerArgumentError("batch_size")
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    @override
    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score candidates in bounded batches and return one global stable ranking."""

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise _InvalidRerankerArgumentError("limit")
        _validate_bounds(len(candidates), limit)

        scores: dict[int, int | float] = {}
        request_count = 0
        for offset in range(0, len(candidates), self._batch_size):
            batch_ranks = tuple(range(offset + 1, min(offset + self._batch_size, len(candidates)) + 1))
            batch = [{"rank": rank, "text": candidates[rank - 1].text} for rank in batch_ranks]
            response = await self._client.post(
                self._endpoint,
                json={"query": query, "candidates": batch},
            )
            request_count += 1
            response.raise_for_status()
            batch_scores = _parse_scores(response, batch_ranks)
            scores.update(batch_scores)

        selected_ranks = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=request_count),
        )


def _parse_scores(response: httpx.Response, batch_ranks: tuple[int, ...]) -> dict[int, int | float]:
    try:
        payload: Any = json.loads(response.content, object_pairs_hook=_reject_duplicate_json_keys)
    except ValueError as error:
        raise _InvalidRerankerResponseError("invalid-json") from error

    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise _InvalidRerankerResponseError("top-level-fields")
    return _parse_score_entries(payload["scores"], batch_ranks)


def _parse_score_entries(values: Any, batch_ranks: tuple[int, ...]) -> dict[int, int | float]:
    if not isinstance(values, list):
        raise _InvalidRerankerResponseError("scores-type")

    expected_ranks = set(batch_ranks)
    observed_ranks: set[int] = set()
    entries: list[tuple[int, int | float]] = []
    for item in values:
        rank, score = _parse_score_entry(item, expected_ranks)
        if rank in observed_ranks:
            raise _InvalidRerankerResponseError("duplicate-rank")
        observed_ranks.add(rank)
        entries.append((rank, score))

    if observed_ranks != expected_ranks:
        raise _InvalidRerankerResponseError("rank-coverage")
    return dict(entries)


def _parse_score_entry(
    item: Any,
    expected_ranks: set[int],
) -> tuple[int, int | float]:
    if not isinstance(item, dict) or set(item) != {"rank", "score"}:
        raise _InvalidRerankerResponseError("entry-fields")
    rank = item["rank"]
    if isinstance(rank, bool) or not isinstance(rank, int):
        raise _InvalidRerankerResponseError("rank-type")
    if rank not in expected_ranks:
        raise _InvalidRerankerResponseError("batch-rank")

    score = item["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise _InvalidRerankerResponseError("score-type")
    if isinstance(score, float) and not math.isfinite(score):
        raise _InvalidRerankerResponseError("score-finite")
    return rank, score


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _InvalidRerankerResponseError("invalid-json", f"duplicate object key {key}")
        value[key] = item
    return value


__all__ = ["HTTPMemoryReranker"]
