"""Write the API's OpenAPI schema to frontend/openapi.json.

    python scripts/export_openapi.py          # rewrite the file
    python scripts/export_openapi.py --check  # exit 1 if it is out of date

The frontend's TypeScript types are generated from this file (``npm run
api:types`` in frontend/), so the schema is the single contract between the two
sides. It is committed, and CI fails when it no longer matches the code, which
turns an API change the frontend hasn't absorbed into a red build instead of a
runtime bug.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "frontend" / "openapi.json"


def render() -> str:
    # Importing the app reads config for CORS; deprecation chatter from
    # dependencies is irrelevant to the schema.
    warnings.simplefilter("ignore")
    sys.path.insert(0, str(REPO / "src"))
    from askmydocs.api.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args()

    schema = render()
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    if args.check:
        if current != schema:
            print(
                f"{TARGET.relative_to(REPO)} is out of date with the API. Run:\n"
                "    python scripts/export_openapi.py\n"
                "    cd frontend && npm run api:types",
                file=sys.stderr,
            )
            return 1
        print(f"{TARGET.relative_to(REPO)} matches the API.")
        return 0

    TARGET.write_text(schema, encoding="utf-8", newline="\n")
    print(f"Wrote {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
