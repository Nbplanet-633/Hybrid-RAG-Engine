"""The committed OpenAPI schema must match the API.

frontend/openapi.json is the contract the web app's TypeScript types are
generated from. If a response model changes and the file isn't regenerated,
the frontend silently keeps the old shape; this test turns that into a failure
with the command that fixes it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _exporter():
    spec = importlib.util.spec_from_file_location(
        "export_openapi", REPO_ROOT / "scripts" / "export_openapi.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_schema_matches_the_api() -> None:
    exporter = _exporter()
    committed = exporter.TARGET.read_text(encoding="utf-8")
    assert committed == exporter.render(), (
        "frontend/openapi.json is stale. Run `python scripts/export_openapi.py`, "
        "then `npm run api:types` in frontend/."
    )


def test_response_fields_are_all_required() -> None:
    # The frontend relies on this: a response always carries every field, so its
    # generated types must not mark any of them optional.
    schemas = json.loads(_exporter().render())["components"]["schemas"]
    for name in ("AskResponse", "CitationOut", "DocumentSummary", "LibraryInfo", "PassageOut"):
        schema = schemas[name]
        assert set(schema["required"]) == set(schema["properties"]), name


def test_requests_keep_their_defaults_optional() -> None:
    schema = json.loads(_exporter().render())["components"]["schemas"]["LibraryAskRequest"]
    assert schema["required"] == ["question"]
