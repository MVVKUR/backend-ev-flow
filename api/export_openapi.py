"""Export the OpenAPI (Swagger) spec to disk without running the server.

Usage:
    python -m api.export_openapi          # writes openapi.json (+ openapi.yaml if PyYAML present)
"""
from __future__ import annotations

import json
from pathlib import Path

from .main import app

OUT_DIR = Path(__file__).resolve().parent.parent
JSON_PATH = OUT_DIR / "openapi.json"
YAML_PATH = OUT_DIR / "openapi.yaml"


def main() -> None:
    spec = app.openapi()
    JSON_PATH.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {JSON_PATH}  ({len(spec.get('paths', {}))} paths)")
    try:
        import yaml  # type: ignore
        YAML_PATH.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"wrote {YAML_PATH}")
    except ImportError:
        print("(PyYAML not installed: skipped openapi.yaml; `pip install pyyaml` to enable)")


if __name__ == "__main__":
    main()
