#!/usr/bin/env python3
"""Dump public-project URLs from the ``/explore`` (`/api/v4/projects`) endpoint
of one or more GitLab instances.

The output is one project URL per line, one file per host — ready to feed back
into ``opc crawl --platforms <host> --seed-file <file>`` for an instance-wide
discovery crawl.

Token resolution follows the same scheme as the crawler itself
(``CRAWLER_TOKEN_POOL__<HOST>`` / ``CRAWLER_TOKEN__<HOST>``); hosts without a
token fall back to anonymous reads — fine for ``visibility=public`` listings
on all three Swiss instances.

Examples
--------
List public projects from the three known Swiss GitLabs, default output dir:

    python tools/scripts/fetch_public_projects.py

Override the host list and cap at 50 projects per host:

    python tools/scripts/fetch_public_projects.py \\
        --hosts gitlab.com,gitlab.epfl.ch \\
        --per-host-limit 50

Pipe directly to a crawl:

    python tools/scripts/fetch_public_projects.py --hosts gitlab.ethz.ch \\
        --output-dir /tmp/explore
    opc crawl --platforms gitlab.ethz.ch --rounds 1 \\
        --seed-file /tmp/explore/gitlab.ethz.ch.txt
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Iterable, List

import gitlab

from open_pulse_crawler.config import resolve_tokens

DEFAULT_HOSTS = [
    "gitlab.epfl.ch",
    "gitlab.ethz.ch",
    "gitlab.renkulab.io",
]
DEFAULT_OUTPUT = Path("data/explore")
DEFAULT_LIMIT_PER_HOST = 1000
DEFAULT_PER_PAGE = 100   # GitLab's hard maximum


def _build_gitlab(host: str) -> gitlab.Gitlab:
    """Construct a :class:`gitlab.Gitlab` for ``host``, anonymous if no token."""
    tokens = resolve_tokens(host)
    if tokens:
        return gitlab.Gitlab(url=f"https://{host}", private_token=tokens[0])
    return gitlab.Gitlab(url=f"https://{host}")


def _total_public_projects(host: str) -> int | None:
    """Best-effort total project count via ``X-Total`` (None when not surfaced)."""
    import httpx

    tokens = resolve_tokens(host)
    headers = {"PRIVATE-TOKEN": tokens[0]} if tokens else {}
    try:
        r = httpx.head(
            f"https://{host}/api/v4/projects",
            params={"visibility": "public", "per_page": 1, "page": 1},
            headers=headers,
            timeout=10.0,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return None
    raw = r.headers.get("x-total") or r.headers.get("X-Total")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def fetch_public_project_urls(host: str, limit: int) -> Iterable[str]:
    """Yield up to ``limit`` public project web URLs from ``host``.

    Uses ``/api/v4/projects?visibility=public`` with ``per_page=100`` and
    page-by-page iteration so we can stop cleanly once ``limit`` is hit
    without buffering the whole instance in memory.
    """
    gl = _build_gitlab(host)
    yielded = 0
    page = 1
    while yielded < limit:
        try:
            batch = gl.projects.list(
                visibility="public",
                per_page=min(DEFAULT_PER_PAGE, limit - yielded),
                page=page,
                get_all=False,
            )
        except gitlab.GitlabError as exc:
            sys.stderr.write(
                f"  ! {host}: page {page} failed ({type(exc).__name__}: {exc}); stopping.\n"
            )
            return
        if not batch:
            return
        for proj in batch:
            url = getattr(proj, "web_url", None)
            if not url:
                continue
            yield url
            yielded += 1
            if yielded >= limit:
                return
        page += 1
        # Gentle pause so anonymous mode doesn't trip per-IP rate limits.
        time.sleep(0.05)


def write_host_urls(host: str, urls: List[str], output_dir: Path) -> Path:
    """Write ``urls`` to ``<output_dir>/<host>.txt`` (one per line)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{host}.txt"
    out.write_text("\n".join(urls) + ("\n" if urls else ""))
    return out


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[1:]),
    )
    parser.add_argument(
        "--hosts",
        default=",".join(DEFAULT_HOSTS),
        help=f"Comma-separated GitLab hosts to query (default: {','.join(DEFAULT_HOSTS)})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Directory to write per-host URL lists (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--per-host-limit",
        type=int,
        default=DEFAULT_LIMIT_PER_HOST,
        help=f"Maximum URLs to write per host (default: {DEFAULT_LIMIT_PER_HOST})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print progress to stderr as pages come in.",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    output_dir: Path = args.output_dir
    limit: int = args.per_host_limit
    verbose: bool = args.verbose

    print(f"Fetching public projects from {len(hosts)} instance(s) into {output_dir}/")
    totals: dict[str, int] = {}
    for host in hosts:
        tokens = resolve_tokens(host)
        mode = f"authenticated ({len(tokens)} token{'s' if len(tokens) != 1 else ''})" if tokens else "anonymous"
        total = _total_public_projects(host)
        target = min(total, limit) if total is not None else limit
        total_repr = f"~{total}" if total is not None else "unknown"
        print(
            f"  {host} [{mode}] total={total_repr} target={target} …",
            flush=True,
        )

        urls: List[str] = []
        start = time.monotonic()
        for url in fetch_public_project_urls(host, limit):
            urls.append(url)
            if verbose and len(urls) % 100 == 0:
                pct = f" ({100*len(urls)/total:.1f}%)" if total else ""
                sys.stderr.write(f"  {host}: {len(urls)}{pct} urls in {time.monotonic()-start:.1f}s\n")

        out_path = write_host_urls(host, urls, output_dir)
        totals[host] = len(urls)
        elapsed = time.monotonic() - start
        print(f"    {host}: {len(urls)} urls in {elapsed:.1f}s → {out_path}")

    grand = sum(totals.values())
    print(f"\nDone. {grand} project URLs across {len(hosts)} host(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
