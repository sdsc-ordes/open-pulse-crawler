#!/usr/bin/env python3
"""
Rename files in a `jsonld/` directory by prefixing a timestamp derived from
filesystem metadata.

Example:
  20260325203707.SwissDataScienceCenter__yagup.json
    -> SwissDataScienceCenter__yagup.20260325161030.json

Notes:
- On Linux/WSL, "creation time" is not always available; we use `st_ctime`
  (metadata-change time) by default.
- Many existing files in `jsonld/` already start with `YYYYMMDDHHMMSS.`.
  By default, this script strips that existing leading timestamp so it
  doesn't double-prefix.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path


def _format_timestamp(ts: float) -> str:
    # Local time by default (matches how you were generating crawl timestamps).
    return dt.datetime.fromtimestamp(ts).strftime("%Y%m%d%H%M%S")


def _strip_existing_timestamp(stem: str) -> str:
    """
    Strip a timestamp if it already exists in either of these forms:
    - '<YYYYMMDDHHMMSS>.<rest>' (leading timestamp prefix)
    - '<rest>.<YYYYMMDDHHMMSS>' (trailing timestamp suffix)
    - '<YYYYMMDD_HHMMSS>.<rest>' (older underscore-separated leading timestamp prefix)
    Otherwise return stem unchanged.
    """
    # Leading: YYYYMMDDHHMMSS.<rest>
    if len(stem) > 15 and stem[:14].isdigit() and stem[14] == ".":
        return stem[15:]

    # YYYYMMDD_HHMMSS.<rest>
    if len(stem) > 19 and stem[:8].isdigit() and stem[8] == "_" and stem[15] == ".":  # pragma: no cover
        # Very specific shape; leave as conservative fallback.
        return stem[16:]

    # Trailing: <rest>.YYYYMMDDHHMMSS
    if len(stem) > 15 and stem[-14:].isdigit() and stem[-15] == ".":
        return stem[:-15]

    return stem


def _rename_path(
    *,
    src: Path,
    dst_dir: Path,
    timestamp: str,
    dry_run: bool,
    strip_existing_ts: bool,
) -> Path:
    assert src.is_file()
    ext = src.suffix  # includes ".json"
    stem = src.stem  # excludes ".json"
    out_stem = _strip_existing_timestamp(stem) if strip_existing_ts else stem

    # Desired filename order:
    #   <name>.<timestamp>.json
    base_name = f"{out_stem}.{timestamp}{ext}"
    dst = dst_dir / base_name

    if not dst.exists():
        if not dry_run:
            src.rename(dst)
        return dst

    # Collision handling: same timestamp (same second) for multiple files.
    i = 1
    while True:
        candidate = dst_dir / f"{out_stem}.{timestamp}_{i}{ext}"
        if not candidate.exists():
            if not dry_run:
                src.rename(candidate)
            return candidate
        i += 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prefix jsonld/*.json with YYYYMMDDHHMMSS from file metadata."
    )
    parser.add_argument(
        "jsonld_dir",
        type=Path,
        help="Path to the jsonld directory containing *.json payloads.",
    )
    parser.add_argument(
        "--mode",
        choices=["ctime", "mtime"],
        default="ctime",
        help="Timestamp source: st_ctime (default, Linux/WSL 'creation-ish') or st_mtime.",
    )
    parser.add_argument(
        "--no-strip",
        action="store_true",
        help="Do not strip an existing leading 'YYYYMMDDHHMMSS.' prefix from filenames.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned renames but do not modify files.",
    )
    args = parser.parse_args()

    jsonld_dir: Path = args.jsonld_dir
    if not jsonld_dir.exists() or not jsonld_dir.is_dir():
        raise SystemExit(f"Not a directory: {jsonld_dir}")

    json_files = sorted(jsonld_dir.glob("*.json"))
    if not json_files:
        print(f"No *.json files found in {jsonld_dir}")
        return

    print(f"Found {len(json_files)} file(s) in {jsonld_dir}")
    strip_existing_ts = not args.no_strip
    for src in json_files:
        st = src.stat()
        ts = st.st_ctime if args.mode == "ctime" else st.st_mtime
        timestamp = _format_timestamp(ts)
        ext = src.suffix
        stem = src.stem
        out_stem = _strip_existing_timestamp(stem) if strip_existing_ts else stem

        planned = jsonld_dir / f"{out_stem}.{timestamp}{ext}"
        print(f"{src.name} -> {planned.name}")

        _rename_path(
            src=src,
            dst_dir=jsonld_dir,
            timestamp=timestamp,
            dry_run=args.dry_run,
            strip_existing_ts=strip_existing_ts,
        )

    if args.dry_run:
        print("Dry-run complete. No files were renamed.")
    else:
        print("Renaming complete.")


if __name__ == "__main__":
    # Ensure consistent timestamp behavior across platforms/containers.
    os.environ.setdefault("TZ", "UTC")
    main()

