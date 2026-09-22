"""Generate packaged JSON Schemas from public Python types, or check for drift."""

import argparse
import json
from pathlib import Path

from foliqant.contracts.schemas import decision_schemas, runtime_schemas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "src" / "foliqant" / "schemas"
    schemas = runtime_schemas()
    decisions = decision_schemas()
    if schemas.keys() & decisions.keys():
        raise ValueError("public schema names must be unique")
    schemas.update(decisions)
    mismatches: list[str] = []
    for name, schema in schemas.items():
        schema["$id"] = f"urn:foliqant:schema:{name}"
        encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        path = output / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != encoded:
                mismatches.append(name)
        else:
            output.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded, encoding="utf-8")
    unexpected = sorted(
        path.name for path in output.glob("*.schema.json") if path.name not in schemas
    )
    if mismatches or unexpected:
        print(json.dumps({"ok": False, "changed": mismatches, "unexpected": unexpected}))
        return 1
    print(json.dumps({"ok": True, "schemas": len(schemas), "checked": args.check}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
