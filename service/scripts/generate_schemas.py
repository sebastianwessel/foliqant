"""Generate service JSON Schemas, or check the committed copies for drift."""

import argparse
import json
from pathlib import Path

from foliqant.contracts.schemas import service_schemas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[2] / "contracts" / "service"
    schemas = service_schemas()
    mismatches: list[str] = []
    for name, schema in schemas.items():
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
