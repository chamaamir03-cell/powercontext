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

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    HTTPMemoryReranker,
    MemoryEntryInput,
    MemoryHit,
    MemoryRerankDecision,
)
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import RuntimeConfig


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=f"entry-{rank}",
        entry_version_id=f"entry-{rank}-v1",
        text=f"Candidate {rank}",
        score=1 / rank,
        matched_by=("fts",),
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://reranker.test",
    )


def _json_response(scores: object) -> httpx.Response:
    return httpx.Response(200, json={"scores": scores})


def _request_body(request: httpx.Request) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(request.content))


def test_http_reranker_posts_one_batch_and_sorts_scores() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _json_response([{"rank": 1, "score": 0}, {"rank": 2, "score": 0.9}])

        async with _client(respond) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            candidates = (_hit(1), _hit(2))
            decision = await reranker.rerank("query", candidates, 2)

        assert decision == MemoryRerankDecision(
            selected_ranks=(2, 1),
            usage=InferenceUsage(requests=1),
        )
        assert requests[0].method == "POST"
        assert json.loads(requests[0].content) == {
            "query": "query",
            "candidates": [
                {"rank": 1, "text": "Candidate 1"},
                {"rank": 2, "text": "Candidate 2"},
            ],
        }

    asyncio.run(scenario())


def test_http_reranker_batches_keep_original_ranks_and_sort_globally() -> None:
    async def scenario() -> None:
        observed: list[list[dict[str, object]]] = []
        batch_scores = {
            1: 0.4,
            2: 0.8,
            3: 0.8,
            4: 0.9,
            5: 0.95,
        }

        def respond(request: httpx.Request) -> httpx.Response:
            body = _request_body(request)
            batch = cast(list[dict[str, Any]], body["candidates"])
            observed.append(batch)
            return _json_response([
                {"rank": candidate["rank"], "score": batch_scores[candidate["rank"]]} for candidate in batch
            ])

        async with _client(respond) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client, batch_size=2)
            decision = await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 6)), 4)

        assert [[candidate["rank"] for candidate in batch] for batch in observed] == [[1, 2], [3, 4], [5]]
        assert all(len(batch) <= 2 for batch in observed)
        assert decision.selected_ranks == (5, 4, 2, 3)
        assert decision.usage.requests == 3
        assert decision.discarded_rank_count == 0
        assert decision.used_fallback is False

    asyncio.run(scenario())


def test_http_reranker_ties_use_original_rank_order() -> None:
    async def scenario() -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            body = _request_body(request)
            return _json_response([{"rank": item["rank"], "score": 1.0} for item in body["candidates"]])

        async with _client(respond) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client, batch_size=2)
            decision = await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 5)), 4)

        assert decision.selected_ranks == (1, 2, 3, 4)
        assert decision.usage.requests == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scores",
    [
        [{"rank": 1, "score": 0.1}],
        [{"rank": 1, "score": 0.1}, {"rank": 1, "score": 0.2}],
        [{"rank": 1, "score": 0.1}, {"rank": 3, "score": 0.2}],
        [{"rank": 1, "score": 0.1, "extra": True}, {"rank": 2, "score": 0.2}],
    ],
    ids=["missing-rank", "duplicate-rank", "other-batch-rank", "extra-item-field"],
)
def test_http_reranker_rejects_invalid_batch_coverage(scores: list[dict[str, object]]) -> None:
    async def scenario() -> None:
        async with _client(lambda _: _json_response(scores)) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client, batch_size=2)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1), _hit(2), _hit(3)), 1)

    asyncio.run(scenario())


def test_http_reranker_rejects_duplicate_rank_even_when_batch_is_covered() -> None:
    async def scenario() -> None:
        scores = [
            {"rank": 1, "score": 0.3},
            {"rank": 1, "score": 0.2},
            {"rank": 2, "score": 0.1},
        ]
        async with _client(lambda _: _json_response(scores)) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client, batch_size=2)
            with pytest.raises(ValueError, match="duplicate ranks"):
                await reranker.rerank("query", (_hit(1), _hit(2)), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response_body", "content"),
    [
        ([], None),
        ({"scores": {}}, None),
        ({"scores": [{"rank": 1, "score": 1}], "extra": True}, None),
        ({"scores": [{"rank": 1}]}, None),
        ({"scores": [{"score": 1}]}, None),
        ({"scores": [{"rank": 1, "score": 1}, {"rank": 2, "score": 2, "extra": 3}]}, None),
        (None, b"{invalid"),
    ],
    ids=[
        "wrong-root",
        "wrong-scores-type",
        "extra-top-field",
        "missing-score-field",
        "missing-rank-field",
        "extra-item-field",
        "invalid-json",
    ],
)
def test_http_reranker_rejects_invalid_response_structure(response_body: object, content: bytes | None) -> None:
    async def scenario() -> None:
        def respond(_: httpx.Request) -> httpx.Response:
            if content is not None:
                return httpx.Response(200, content=content)
            return httpx.Response(200, json=response_body)

        async with _client(respond) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1), _hit(2)), 2)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content",
    [
        b'{"scores":[],"scores":[]}',
        b'{"scores":[{"rank":1,"rank":1,"score":0.5}]}',
    ],
    ids=["duplicate-top-level-key", "duplicate-score-rank-key"],
)
def test_http_reranker_rejects_duplicate_json_object_keys(content: bytes) -> None:
    async def scenario() -> None:
        async with _client(lambda _: httpx.Response(200, content=content)) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(ValueError, match="valid JSON"):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("rank", "score"),
    [
        (1, float("nan")),
        (1, float("inf")),
        (1, float("-inf")),
        (1, True),
        (True, 1.0),
        (1.0, 1),
        (1, "1"),
        (0, 1.0),
        (-1, 1.0),
    ],
    ids=[
        "nan",
        "positive-infinity",
        "negative-infinity",
        "boolean-score",
        "boolean-rank",
        "float-rank",
        "string-score",
        "zero-rank",
        "negative-rank",
    ],
)
def test_http_reranker_rejects_invalid_numeric_types(rank: object, score: object) -> None:
    async def scenario() -> None:
        async with _client(lambda _: _json_response([{"rank": rank, "score": score}])) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_http_reranker_propagates_http_and_network_errors() -> None:
    async def scenario() -> None:
        async with _client(lambda _: httpx.Response(503)) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await reranker.rerank("query", (_hit(1),), 1)

        request = httpx.Request("POST", "https://reranker.test/score")

        def fail(_: httpx.Request) -> httpx.Response:
            message = f"connection failed for {request.url}"
            raise httpx.ConnectError(message, request=request)

        async with _client(fail) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(httpx.ConnectError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_http_reranker_propagates_timeout_error() -> None:
    async def scenario() -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            message = "request timed out"
            raise httpx.ReadTimeout(message, request=request)

        async with _client(fail) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(httpx.ReadTimeout):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_http_reranker_does_not_return_partial_result_when_second_batch_fails() -> None:
    async def scenario() -> None:
        observed_ranks: list[list[int]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            candidates = _request_body(request)["candidates"]
            ranks = [candidate["rank"] for candidate in candidates]
            observed_ranks.append(ranks)
            if len(observed_ranks) == 2:
                message = "second batch failed"
                raise httpx.ConnectError(message, request=request)
            return _json_response([{"rank": rank, "score": 1.0} for rank in ranks])

        async with _client(respond) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client, batch_size=2)
            with pytest.raises(httpx.ConnectError):
                await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 5)), 2)

        assert observed_ranks == [[1, 2], [3, 4]]

    asyncio.run(scenario())


def test_http_reranker_sorts_negative_zero_and_large_finite_scores() -> None:
    async def scenario() -> None:
        score_by_rank = {1: -2.0, 2: 0.0, 3: 1.5, 4: 1e308, 5: 1.5}

        def respond(request: httpx.Request) -> httpx.Response:
            candidates = _request_body(request)["candidates"]
            return _json_response([{"rank": item["rank"], "score": score_by_rank[item["rank"]]} for item in candidates])

        async with _client(respond) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            decision = await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 6)), 5)

        assert decision.selected_ranks == (4, 3, 5, 2, 1)
        assert decision.usage.requests == 1

    asyncio.run(scenario())


def test_http_reranker_rejects_rank_above_candidate_count() -> None:
    async def scenario() -> None:
        async with _client(lambda _: _json_response([{"rank": 1, "score": 1.0}, {"rank": 3, "score": 0.5}])) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1), _hit(2)), 2)

    asyncio.run(scenario())


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_http_reranker_rejects_invalid_batch_size(batch_size: object) -> None:
    async def scenario() -> None:
        async with _client(lambda _: _json_response([])) as client:
            with pytest.raises(ValueError):
                HTTPMemoryReranker(
                    "https://reranker.test/score",
                    client=client,
                    batch_size=cast(int, batch_size),
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, -1, 2, True, 1.5])
def test_http_reranker_rejects_invalid_limit(limit: object) -> None:
    async def scenario() -> None:
        async with _client(lambda _: _json_response([])) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), cast(int, limit))

    asyncio.run(scenario())


def test_http_reranker_rejects_empty_candidates() -> None:
    async def scenario() -> None:
        async with _client(lambda _: _json_response([])) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (), 1)

    asyncio.run(scenario())


def test_runtime_memory_search_preserves_hit_identity_with_http_reranker() -> None:
    async def scenario() -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            body = _request_body(request)
            candidates = body["candidates"]
            return _json_response([
                {"rank": candidate["rank"], "score": 1.0 if candidate["rank"] == 2 else 0.0} for candidate in candidates
            ])

        async with _client(respond) as client:
            reranker = HTTPMemoryReranker(
                "https://reranker.test/score",
                client=client,
                batch_size=2,
            )
            config = BuiltinConfig(runtime=RuntimeConfig(memory_rerank_candidate_limit=4))
            async with open_builtin_contexts(config, memory_reranker=reranker) as contexts:
                service = (await contexts.get("http-rerank")).artifacts.memory
                memory = await service.remember(
                    memory=None,
                    entries=tuple(MemoryEntryInput(kind="fact", text=f"Project fact {rank}.") for rank in range(1, 5)),
                    mode="append",
                )
                assert memory is not None

                result = await service.search("project", memories=(memory,), limit=2, mode="fts")

        assert result.rerank is not None
        assert result.rerank.policy_id == "http-cross-encoder-v1"
        assert result.rerank.usage.requests == 2
        assert len(result.rerank.candidate_hits) == 4
        expected = result.rerank.candidate_hits[1]
        selected = result.hits[0]
        assert selected.memory_ref == expected.memory_ref
        assert selected.entry_id == expected.entry_id
        assert selected.entry_version_id == expected.entry_version_id
        assert selected.text == expected.text

    asyncio.run(scenario())
