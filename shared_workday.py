import json
from pathlib import Path
from tempfile import TemporaryDirectory

from demo import run_workday


def load_sample():
    path = Path(__file__).with_name("samples") / "shared_payload.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    with TemporaryDirectory(prefix="shared-reels-") as directory:
        run_workday(load_sample(), Path(directory))


if __name__ == "__main__":
    main()
