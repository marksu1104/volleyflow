"""Fail if any GitHub Actions workflow file doesn't parse.

A malformed workflow doesn't error — it silently doesn't run, and the
run appears under its filename instead of its name. A broken
deploy-pages.yml shipped exactly that way: CI green, deploy skipped, and
the site left on the previous build while the app looked healthy.
"""

import pathlib
import sys

import yaml


def main() -> int:
    problems: list[str] = []
    for path in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{path}: {exc}")
            continue
        if not isinstance(parsed, dict) or not parsed.get("name"):
            problems.append(f"{path}: no top-level name")

    for problem in problems:
        print(problem)
    if not problems:
        print("all workflow files parse")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
