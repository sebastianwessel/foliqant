"""Generate public Foliqant JSON Schemas, or check the committed copies for drift."""

import argparse
import json
from pathlib import Path

from foliqant.contracts.schemas import decision_schemas, runtime_schemas

SCHEMA_SETS = {
    "runtime": runtime_schemas,
    "decisions": decision_schemas,
}


def encoded_schema(scope: str, name: str, schema: object) -> str:
    assert isinstance(schema, dict)
    schema["$id"] = f"https://foliqant.local/schemas/foliqant/{scope}/{name}"
    return json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "schemas" / "foliqant"
    mismatches: list[str] = []
    unexpected: list[str] = []
    schema_count = 0
    for scope, build_schemas in SCHEMA_SETS.items():
        schemas = build_schemas()
        scope_output = output / scope
        schema_count += len(schemas)
        for name, schema in schemas.items():
            encoded = encoded_schema(scope, name, schema)
            path = scope_output / name
            if args.check:
                if not path.is_file() or path.read_text(encoding="utf-8") != encoded:
                    mismatches.append(f"{scope}/{name}")
            else:
                scope_output.mkdir(parents=True, exist_ok=True)
                path.write_text(encoded, encoding="utf-8")
        unexpected.extend(
            f"{scope}/{path.name}"
            for path in scope_output.glob("*.schema.json")
            if path.name not in schemas
        )
    if mismatches or unexpected:
        print(json.dumps({"ok": False, "changed": mismatches, "unexpected": sorted(unexpected)}))
        return 1
    print(json.dumps({"ok": True, "schemas": schema_count, "checked": args.check}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
