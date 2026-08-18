"""HTTP API tests.

The API builds its pipeline in a lifespan handler from the ambient configuration,
so these tests point ASKMYDOCS_* at a tmp_path corpus and use TestClient as a
context manager to trigger startup.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(
    tmp_path: Path, corpus_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("ASKMYDOCS_PROFILE", "offline")
    monkeypatch.setenv("ASKMYDOCS_CONFIG", str(REPO_ROOT / "config" / "app.yaml"))
    monkeypatch.setenv("ASKMYDOCS_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("ASKMYDOCS_CORPUS_DIR", str(corpus_dir))

    from askmydocs.api.main import app

    with TestClient(app) as test_client:
        test_client.post("/ingest", json={"targets": [], "reset": True})
        yield test_client


@pytest.fixture
def empty_client(
    tmp_path: Path, corpus_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """A client whose index was never populated."""
    monkeypatch.setenv("ASKMYDOCS_PROFILE", "offline")
    monkeypatch.setenv("ASKMYDOCS_CONFIG", str(REPO_ROOT / "config" / "app.yaml"))
    monkeypatch.setenv("ASKMYDOCS_STORAGE_DIR", str(tmp_path / "empty-storage"))
    monkeypatch.setenv("ASKMYDOCS_CORPUS_DIR", str(corpus_dir))

    from askmydocs.api.main import app

    with TestClient(app) as test_client:
        yield test_client


class TestOps:
    def test_healthz_is_ok(self, client: TestClient) -> None:
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert body["version"]

    def test_readyz_reports_ready_when_indexed(self, client: TestClient) -> None:
        body = client.get("/readyz").json()
        assert body["status"] == "ready"
        assert body["chunks"] > 0

    def test_readyz_fails_on_an_empty_index(self, empty_client: TestClient) -> None:
        # An orchestrator must not route traffic to a node that can only abstain.
        response = empty_client.get("/readyz")
        assert response.status_code == 503
        assert "empty" in response.json()["detail"].lower()

    def test_stats_exposes_the_running_configuration(self, client: TestClient) -> None:
        body = client.get("/stats").json()
        assert body["profile"] == "offline"
        assert body["chunks"] > 0
        assert body["prompt"].startswith("answer.")
        assert body["prompt_hash"]

    def test_prompts_lists_the_registry_and_the_active_version(self, client: TestClient) -> None:
        body = client.get("/prompts").json()
        assert body["active"]["name"] == "answer"
        assert body["active"]["version"] == "v2"
        names = {(p["name"], p["version"]) for p in body["available"]}
        assert ("answer", "v1") in names and ("answer", "v2") in names

    def test_openapi_schema_is_served(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/ask" in schema["paths"]
        assert "/ingest" in schema["paths"]


class TestAsk:
    def test_returns_a_cited_answer(self, client: TestClient) -> None:
        body = client.post(
            "/ask", json={"question": "How long do I have to request a refund?"}
        ).json()
        assert body["abstained"] is False
        assert "14" in body["answer"]
        assert body["citations"]
        assert body["citations"][0]["marker"] == "S1"
        assert body["citations"][0]["source"].endswith("handbook.md")
        assert body["prompt"] == "answer.v2"
        assert body["latency_ms"] >= 0

    def test_abstains_with_a_machine_readable_reason(self, client: TestClient) -> None:
        body = client.post("/ask", json={"question": "What is the capital of Portugal?"}).json()
        assert body["abstained"] is True
        assert body["abstain_reason"]
        assert body["citations"] == []
        assert body["confidence"] == 0.0

    def test_retrieval_trace_is_opt_in(self, client: TestClient) -> None:
        without = client.post("/ask", json={"question": "refund window"}).json()
        assert without["retrieval"] is None

        with_trace = client.post(
            "/ask", json={"question": "refund window", "include_retrieval": True}
        ).json()
        assert with_trace["retrieval"]
        first = with_trace["retrieval"][0]
        assert first["rank"] == 1
        assert first["retriever"] in {"hybrid", "dense", "lexical"}

    def test_top_n_is_honoured(self, client: TestClient) -> None:
        body = client.post(
            "/ask", json={"question": "refund window", "top_n": 2, "include_retrieval": True}
        ).json()
        assert len(body["retrieval"]) <= 2

    def test_blank_question_is_a_validation_error(self, client: TestClient) -> None:
        assert client.post("/ask", json={"question": ""}).status_code == 422

    def test_missing_question_is_a_validation_error(self, client: TestClient) -> None:
        assert client.post("/ask", json={}).status_code == 422

    def test_overlong_question_is_rejected(self, client: TestClient) -> None:
        assert client.post("/ask", json={"question": "x" * 5000}).status_code == 422

    def test_top_n_out_of_range_is_rejected(self, client: TestClient) -> None:
        assert client.post("/ask", json={"question": "refund", "top_n": 99}).status_code == 422

    def test_asking_before_ingesting_is_a_conflict(self, empty_client: TestClient) -> None:
        response = empty_client.post("/ask", json={"question": "anything"})
        assert response.status_code == 409
        assert "ingest" in response.json()["detail"].lower()


class TestIngest:
    def test_ingest_reports_what_it_did(self, empty_client: TestClient) -> None:
        body = empty_client.post("/ingest", json={"targets": [], "reset": True}).json()
        assert body["documents"] == 2
        assert body["chunks"] > 0
        assert body["total_chunks"] == body["chunks"]
        assert len(body["sources"]) == 2

    def test_second_ingest_skips_unchanged_documents(self, client: TestClient) -> None:
        body = client.post("/ingest", json={"targets": []}).json()
        assert body["documents"] == 0
        assert body["skipped_unchanged"] == 2

    def test_missing_target_is_a_bad_request(self, client: TestClient) -> None:
        response = client.post("/ingest", json={"targets": ["/nonexistent/path"]})
        assert response.status_code == 400


class TestApiKeyAuth:
    def test_requests_are_rejected_without_the_configured_key(
        self, tmp_path: Path, corpus_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASKMYDOCS_PROFILE", "offline")
        monkeypatch.setenv("ASKMYDOCS_CONFIG", str(REPO_ROOT / "config" / "app.yaml"))
        monkeypatch.setenv("ASKMYDOCS_STORAGE_DIR", str(tmp_path / "auth-storage"))
        monkeypatch.setenv("ASKMYDOCS_CORPUS_DIR", str(corpus_dir))
        monkeypatch.setenv("ASKMYDOCS_API_KEY", "s3cret")

        from askmydocs.api.main import app

        with TestClient(app) as client:
            assert client.post("/ask", json={"question": "refund"}).status_code == 401
            assert (
                client.post(
                    "/ask", json={"question": "refund"}, headers={"X-API-Key": "wrong"}
                ).status_code
                == 401
            )
            # Ops endpoints stay open so health checks work without the secret.
            assert client.get("/healthz").status_code == 200

            client.post(
                "/ingest", json={"targets": [], "reset": True}, headers={"X-API-Key": "s3cret"}
            )
            ok = client.post(
                "/ask",
                json={"question": "How long do I have to request a refund?"},
                headers={"X-API-Key": "s3cret"},
            )
            assert ok.status_code == 200
