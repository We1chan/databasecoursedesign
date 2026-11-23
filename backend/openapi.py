"""Export the published frontend contract."""

import json
from pathlib import Path

from .main import app


def main():
    target = Path(__file__).resolve().parents[1] / "docs" / "05-openapi.json"
    document = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True)
    target.write_text(document + "\n", encoding="utf8", newline="\n")


if __name__ == "__main__":
    main()
