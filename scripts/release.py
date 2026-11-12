#!/usr/bin/env python3
"""Create a release entry and automatically bump the app's displayed version."""

import argparse
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_HEADER = re.compile(
    r"^## \[(\d+)\.(\d+)\.(\d+)\] - \d{4}-\d{2}-\d{2}$", re.MULTILINE
)


def main():
    parser = argparse.ArgumentParser(
        description="Record a release and automatically update the displayed app version."
    )
    parser.add_argument(
        "release_type",
        choices=("feature", "fix", "breaking"),
        help="feature increments the minor version, fix the patch, breaking the major",
    )
    parser.add_argument("summary", help="Short description of this release")
    args = parser.parse_args()
    summary = args.summary.strip()
    if not summary:
        parser.error("Release summary cannot be empty.")

    changelog = CHANGELOG.read_text(encoding="utf-8")
    match = VERSION_HEADER.search(changelog)
    if match is None:
        parser.error(f"No semantic version found in {CHANGELOG}")

    major, minor, patch = map(int, match.groups())
    if args.release_type == "feature":
        minor += 1
        patch = 0
    elif args.release_type == "fix":
        patch += 1
    else:
        major += 1
        minor = 0
        patch = 0
    version = f"{major}.{minor}.{patch}"
    release_entry = (
        f"## [{version}] - {date.today().isoformat()}\n\n"
        f"- {summary}\n\n"
    )
    updated_changelog = (
        changelog[: match.start()]
        + release_entry
        + changelog[match.start() :]
    )
    CHANGELOG.write_text(updated_changelog, encoding="utf-8")
    print(f"Recorded release {version} in CHANGELOG.md.")
    print("Restart the app to display the new version.")


if __name__ == "__main__":
    main()
