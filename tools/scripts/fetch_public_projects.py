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
from typing import Any, Dict, Iterable, List, Optional

import gitlab

from open_pulse_crawler.config import resolve_tokens

# Self-hosted GitLab instances at Swiss research institutions and universities.
# Compiled 2026-05-28; probed at the same date — only hosts that answered
# `GET /api/v4/projects?visibility=public&per_page=1` with HTTP 200 are listed.
#
# Excluded (and why):
#   - gitlab.psi.ch / git-ext.psi.ch      retired 2025-11-30; PSI moved to gitea.psi.ch.
#   - c4science.ch                        retired 2025-07-31; EPFL migrated to gitlab.epfl.ch.
#   - phd-gitlab.ethz.ch, gitlab.aiub.unibe.ch, spacegit.unibe.ch,
#     vit-gitlab.unil.ch, gitlab.ci.inf.usi.ch, git.cscs.ch
#                                         DNS / TCP unreachable on the probe date.
#   - gitlab.enterpriselab.ch             301 → labservices.ch (no longer a GitLab forge).
#   - git.bfh.ch                          legacy gitweb, not a GitLab API target.
#   - github.zhaw.ch                      GitHub Enterprise, not GitLab.
#   - gitlabext.wsl.ch, git.wsl.ch        same backend as code.wsl.ch (proxy aliases).
#
# Each host carries its institution + access mode in a comment so an operator
# can decide which subset to include.
DEFAULT_HOSTS = [
    # --- ETH Domain ---
    "gitlab.ethz.ch",             # ETH Zurich, central
    "gitlab.inf.ethz.ch",         # ETH Zurich, D-INFK
    "git.ee.ethz.ch",             # ETH Zurich, D-ITET
    "sissource.ethz.ch",          # ETH Zurich, Scientific IT Services
    "gitlab.epfl.ch",             # EPFL, central
    "gitlab.empa.ch",             # Empa (0 public projects on probe, but reachable)
    "gitlab.eawag.ch",            # Eawag
    "code.wsl.ch",                # WSL (gitlabext.wsl.ch + git.wsl.ch are aliases)
    # --- Cantonal universities ---
    "gitlab.uzh.ch",              # UZH, central (SWITCH-hosted)
    "gitlab.ifi.uzh.ch",          # UZH, Informatics
    "gitlab.inf.unibe.ch",        # Uni Bern, CS Institute
    "gitlab.climate.unibe.ch",    # Uni Bern, KUP/Climate
    "gitlab.iml.unibe.ch",        # Uni Bern, IML
    "git.upd.unibe.ch",           # Uni Bern, University Psychiatric Services
    "gitlab.unige.ch",            # Uni Geneva
    "gitlabcse.unil.ch",          # Uni Lausanne, CSE
    "git.scicore.unibas.ch",      # Uni Basel, sciCORE
    "cs-gitlab.unine.ch",         # Uni Neuchâtel, IIUN
    # --- Universities of Applied Sciences ---
    "gitlab.ti.bfh.ch",           # BFH Bern
    "gitlab.fhnw.ch",             # FHNW
    "gitlab.hevs.ch",             # HES-SO Valais/Wallis
    "gitlab.forge.hefr.ch",       # HES-SO Fribourg
    "gitlab.ost.ch",              # OST Ostschweizer Fachhochschule
    # --- National research institutions ---
    "gitlab.switch.ch",           # SWITCH (shared, best-effort)
    "gitlab.sib.swiss",           # SIB, general
    "git.dcc.sib.swiss",          # SIB, Data Coordination Centre
    "gitlab.idiap.ch",            # Idiap Research Institute
    "gitlab.renkulab.io",         # SDSC / RenkuLab (legacy; retiring)
    # --- Zenodo (Spec 2) ---
    "zenodo.org",
    # NOTE: sandbox.zenodo.org excluded from defaults — operator-opt-in via --hosts.
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
    """Best-effort total project count via ``X-Total`` (None when not surfaced).

    Returns ``None`` for Zenodo hosts — their ``/api/records`` endpoint uses
    keyset pagination and does not surface an ``X-Total`` header.
    """
    if host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
        return None  # Zenodo uses keyset pagination; no global count header
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


def _fetch_zenodo_urls(host: str, limit: int) -> Iterable[str]:
    """Yield up to ``limit`` Zenodo record URLs by paginating ``/api/records``.

    Uses Zenodo's keyset pagination (``links.next``) — the same pattern the
    ``ZenodoClient`` uses internally.
    """
    import httpx

    tokens = resolve_tokens(host)
    headers = {"Accept": "application/json"}
    if tokens:
        headers["Authorization"] = f"Bearer {tokens[0]}"

    url = f"https://{host}/api/records"
    params: Optional[Dict[str, Any]] = {"size": min(100, limit)}
    yielded = 0
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True) as session:
        while yielded < limit:
            try:
                r = session.get(url, params=params, headers=headers)
                r.raise_for_status()
            except Exception as exc:
                sys.stderr.write(
                    f"  ! {host}: page failed ({type(exc).__name__}: {exc}); stopping.\n"
                )
                return
            body = r.json()
            hits = body.get("hits", {}).get("hits", [])
            if not hits:
                return
            for hit in hits:
                rec_id = hit.get("id")
                if rec_id is None:
                    continue
                yield f"https://{host}/records/{rec_id}"
                yielded += 1
                if yielded >= limit:
                    return
            next_url = body.get("links", {}).get("next")
            if not next_url:
                return
            url = next_url
            params = None   # next URL has params baked in
            time.sleep(0.05)


def fetch_public_project_urls(host: str, limit: int) -> Iterable[str]:
    """Yield up to ``limit`` public project / record URLs from ``host``.

    Dispatches between GitLab's ``/api/v4/projects`` and Zenodo's
    ``/api/records`` based on the host name. GitLab uses page-by-page
    iteration; Zenodo uses keyset pagination via ``links.next``.
    """
    if host in ("zenodo.org", "sandbox.zenodo.org") or host.endswith(".zenodo.org"):
        yield from _fetch_zenodo_urls(host, limit)
        return
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
        except Exception as exc:
            # Catch anything — gitlab.GitlabError covers API-level failures
            # but `requests`-level connection errors (DNS, TLS, timeout)
            # propagate as `requests.exceptions.ConnectionError` and would
            # otherwise tank the whole script when a single host is down.
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
