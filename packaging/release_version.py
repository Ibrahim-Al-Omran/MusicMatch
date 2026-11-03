"""Assign reproducible app versions for automatic main releases and explicit tags."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    if match is None:
        raise ValueError(f"Not a stable release version: {value}")
    return tuple(int(part) for part in match.groups())


def choose_version(base: str, tags: list[str], ref: str) -> tuple[str, bool]:
    version = parse_version(base)
    if ref.startswith("refs/tags/"):
        version = parse_version(ref.removeprefix("refs/tags/"))
    elif ref == "refs/heads/main":
        versions = []
        for tag in tags:
            if not tag.startswith("v"):
                continue
            try:
                versions.append(parse_version(tag))
            except ValueError:
                continue
        if versions:
            latest = max(versions)
            version = max(version, (latest[0], latest[1], latest[2] + 1))
    return ".".join(str(part) for part in version), ref == "refs/heads/main" or ref.startswith("refs/tags/")


def write_version(root: Path, version: str) -> None:
    parse_version(version)
    module = root / "musicmatch" / "__init__.py"
    source, count = re.subn(r'(?m)^__version__ = "[^"]+"$', f'__version__ = "{version}"',
                            module.read_text(encoding="utf-8"))
    if count != 1:
        raise ValueError("Expected exactly one app version declaration")
    module.write_text(source, encoding="utf-8")
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", metavar="VERSION")
    args = parser.parse_args()
    if args.write:
        write_version(ROOT, args.write)
        return
    tags = subprocess.check_output(["git", "tag", "--list"], cwd=ROOT, text=True).splitlines()
    version, publish = choose_version((ROOT / "VERSION").read_text().strip(), tags, os.environ["GITHUB_REF"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"version={version}\ntag=v{version}\npublish={str(publish).lower()}\n")


if __name__ == "__main__":
    main()
