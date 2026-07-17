"""Export the FastAPI-generated OpenAPI contract to api/openapi.yaml
(CONVENTIONS.md: spec kept in sync with handlers — regenerate via `make openapi`)."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.main import create_app


def main() -> None:
    app = create_app()
    spec = app.openapi()
    out = Path(__file__).resolve().parents[1] / "api" / "openapi.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
    print(f"wrote {out} ({len(spec.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
