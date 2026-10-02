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


POLICY_MD = b"""# Leave Policy

## Notice period

The notice period for resignation is 60 days for permanent staff.
"""


def _upload(client: TestClient, name: str, data: bytes, **kwargs):
    return client.post("/library/documents", files={"file": (name, data)}, **kwargs)


class TestLibrary:
    def test_library_starts_empty_and_separate_from_the_corpus(self, client: TestClient) -> None:
        # The corpus is indexed by the fixture; the library must not see it.
        assert client.get("/library/documents").json() == []
        response = client.post("/library/ask", json={"question": "What is the refund window?"})
        assert response.status_code == 409
        assert "empty" in response.json()["detail"].lower()

    def test_upload_list_ask_delete_round_trip(self, client: TestClient) -> None:
        created = _upload(client, "leave-policy.md", POLICY_MD)
        assert created.status_code == 201
        doc = created.json()
        assert doc["filename"] == "leave-policy.md"
        assert doc["chunks"] >= 1

        assert [d["doc_id"] for d in client.get("/library/documents").json()] == [doc["doc_id"]]

        answer = client.post(
            "/library/ask",
            json={"question": "What is the notice period?", "doc_ids": [doc["doc_id"]]},
        ).json()
        assert answer["abstained"] is False
        assert "60 days" in answer["answer"]
        assert answer["citations"][0]["source"].endswith("leave-policy.md")

        assert client.delete(f"/library/documents/{doc['doc_id']}").status_code == 204
        assert client.get("/library/documents").json() == []
        assert client.delete(f"/library/documents/{doc['doc_id']}").status_code == 404

    def test_corpus_ask_is_unaffected_by_uploads(self, client: TestClient) -> None:
        _upload(client, "leave-policy.md", POLICY_MD)
        body = client.post(
            "/ask", json={"question": "What is the notice period?", "include_retrieval": True}
        ).json()
        assert all(not r["source"].endswith("leave-policy.md") for r in body["retrieval"] or [])

    @pytest.mark.parametrize(
        ("name", "data", "code"),
        [
            ("photo.png", b"\x89PNG\r\n", 415),
            ("empty.md", b"", 422),
            ("broken.pdf", b"not a pdf", 422),
        ],
    )
    def test_bad_uploads_get_a_specific_status(
        self, client: TestClient, name: str, data: bytes, code: int
    ) -> None:
        response = _upload(client, name, data)
        assert response.status_code == code
        assert response.json()["detail"]

    def test_oversized_upload_is_413(
        self, tmp_path: Path, corpus_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASKMYDOCS_PROFILE", "offline")
        monkeypatch.setenv("ASKMYDOCS_CONFIG", str(REPO_ROOT / "config" / "app.yaml"))
        monkeypatch.setenv("ASKMYDOCS_STORAGE_DIR", str(tmp_path / "storage"))
        monkeypatch.setenv("ASKMYDOCS_MAX_UPLOAD_MB", "0.001")

        from askmydocs.api.main import app

        with TestClient(app) as client:
            response = _upload(client, "big.md", b"# Big\n\n" + b"word " * 1000)
        assert response.status_code == 413

    def test_duplicate_doc_id_is_a_conflict(self, client: TestClient) -> None:
        doc = b"---\ndoc_id: shared\n---\n\n# One\n\nSome content here.\n"
        assert _upload(client, "one.md", doc).status_code == 201
        assert _upload(client, "two.md", doc).status_code == 409

    def test_asking_an_unknown_document_is_404(self, client: TestClient) -> None:
        _upload(client, "leave-policy.md", POLICY_MD)
        response = client.post(
            "/library/ask", json={"question": "notice period", "doc_ids": ["nope"]}
        )
        assert response.status_code == 404

    def test_empty_doc_ids_list_is_a_validation_error(self, client: TestClient) -> None:
        _upload(client, "leave-policy.md", POLICY_MD)
        response = client.post("/library/ask", json={"question": "notice", "doc_ids": []})
        assert response.status_code == 422

    def test_mutations_require_the_api_key_when_configured(
        self, tmp_path: Path, corpus_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASKMYDOCS_PROFILE", "offline")
        monkeypatch.setenv("ASKMYDOCS_CONFIG", str(REPO_ROOT / "config" / "app.yaml"))
        monkeypatch.setenv("ASKMYDOCS_STORAGE_DIR", str(tmp_path / "storage"))
        monkeypatch.setenv("ASKMYDOCS_API_KEY", "s3cret")

        from askmydocs.api.main import app

        with TestClient(app) as client:
            assert _upload(client, "leave-policy.md", POLICY_MD).status_code == 401
            ok = _upload(client, "leave-policy.md", POLICY_MD, headers={"X-API-Key": "s3cret"})
            assert ok.status_code == 201
            doc_id = ok.json()["doc_id"]
            assert client.delete(f"/library/documents/{doc_id}").status_code == 401
            assert client.post("/library/ask", json={"question": "notice"}).status_code == 401
