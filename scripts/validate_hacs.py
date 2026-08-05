from __future__ import annotations

import json
from pathlib import Path
import sys

REQUIRED_KEYS = {"name", "content_in_root", "render_readme", "homeassistant"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    hacs_file = root / "hacs.json"
    if not hacs_file.exists():
        print("ERROR: hacs.json not found.")
        return 1

    try:
        data = json.loads(hacs_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: hacs.json is not valid JSON: {exc}")
        return 1

    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        print(f"ERROR: hacs.json is missing required keys: {', '.join(missing)}")
        return 1

    if not isinstance(data["homeassistant"], str) or not data["homeassistant"].strip():
        print("ERROR: hacs.json must contain a non-empty string value for 'homeassistant'.")
        return 1

    print("HACS metadata validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
