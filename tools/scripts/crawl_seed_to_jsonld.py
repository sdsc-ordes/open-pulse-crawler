#!/usr/bin/env python3
"""
Run a BFS crawl from one GitHub seed, then fetch JSON-LD for each discovered repo.

Writes one file per repo into a single output directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import httpx

from open_pulse_crawler.crawler import GitHubCrawler
from open_pulse_crawler.gimie_client import clear_stale_jsonld_error_files
from open_pulse_crawler.github_client import GitHubClient
from open_pulse_crawler.token_env import resolve_github_tokens, tokens_not_set_message


def _parse_github_tokens() -> List[str]:
    tokens = resolve_github_tokens()
    if not tokens:
        raise SystemExit(tokens_not_set_message())
    return tokens


def _repo_full_name_to_parts(repo_full_name: str) -> Tuple[str, str]:
    """
    repo_full_name: "owner/repo"
    returns: (repo_name="repo", github_url="https://github.com/owner/repo")
    """
    if "/" not in repo_full_name:
        # If caller gives just "repo", best effort.
        repo_name = repo_full_name
        github_url = f"https://github.com/{repo_full_name}"
        return repo_name, github_url
    owner, repo = repo_full_name.split("/", 1)
    return repo, f"https://github.com/{owner}/{repo}"


def _safe_filename_for_repo(repo_full_name: str) -> str:
    # Keep stable mapping for "owner/repo" -> "owner_repo.json"
    return repo_full_name.replace("/", "_").replace("\\", "_")

def _safe_fs_component(s: str) -> str:
    """
    Make a filesystem component safe.
    Allowed: [a-zA-Z0-9._-] ; everything else becomes '_'.
    """
    import re

    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)


def _jsonld_payload_filename(repo_full_name: str) -> str:
    """
    Filename for JSON-LD payloads using the user/org-repo identity:
      owner__repo.json
    No URL-encoded characters; '/' is replaced via splitting + sanitization.
    """
    owner, repo = _split_repo_full_name(repo_full_name)
    owner_s = _safe_fs_component(owner or "unknown_owner")
    repo_s = _safe_fs_component(repo or "unknown_repo")
    return f"{owner_s}__{repo_s}.json"


def _jsonld_error_filename(repo_full_name: str, suffix: str) -> str:
    owner, repo = _split_repo_full_name(repo_full_name)
    owner_s = _safe_fs_component(owner or "unknown_owner")
    repo_s = _safe_fs_component(repo or "unknown_repo")
    return f"{owner_s}__{repo_s}.{suffix}.json"


def _split_repo_full_name(repo_full_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Split 'owner/repo' into (owner, repo)."""
    if "/" not in repo_full_name:
        return None, repo_full_name
    owner, repo = repo_full_name.split("/", 1)
    return owner, repo


def _jsonld_url(
    *,
    api_base: str,
    jsonld_repo_segment: str,
    repo_full_name: str,
) -> str:
    _, github_url = _repo_full_name_to_parts(repo_full_name)
    encoded_github_url = quote(github_url, safe="")
    base = (
        f"{api_base.rstrip('/')}/v1/repository/{jsonld_repo_segment}/json-ld/{encoded_github_url}"
    )
    # Always ask gimie to bypass its cache when we perform an HTTP fetch.
    return f"{base}?force_refresh=true"


async def _fetch_one_jsonld(
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    repo_full_name: str,
    api_base: str,
    jsonld_repo_segment: str,
    out_dir: Path,
    skip_existing: bool,
    timeout_s: float,
    max_retries: int,
) -> Dict[str, Any]:
    jsonld_dir = out_dir / "jsonld"
    errors_dir = out_dir / "jsonld_errors"
    jsonld_dir.mkdir(parents=True, exist_ok=True)
    errors_dir.mkdir(parents=True, exist_ok=True)

    out_path = jsonld_dir / _jsonld_payload_filename(repo_full_name)
    rel_payload_path = Path("jsonld") / _jsonld_payload_filename(repo_full_name)

    if skip_existing and out_path.exists():
        clear_stale_jsonld_error_files(errors_dir, repo_full_name)
        return {"repo": repo_full_name, "status": "skipped", "jsonld_rel_path": str(rel_payload_path)}

    url = _jsonld_url(
        api_base=api_base,
        jsonld_repo_segment=jsonld_repo_segment,
        repo_full_name=repo_full_name,
    )

    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_retries + 1):
        try:
            async with semaphore:
                start_t = asyncio.get_event_loop().time()
                resp = await client.get(
                    url,
                    headers={"accept": "application/json"},
                    timeout=timeout_s,
                )
                elapsed_s = asyncio.get_event_loop().time() - start_t

            if 200 <= resp.status_code < 300:
                data: Any = resp.json()
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                clear_stale_jsonld_error_files(errors_dir, repo_full_name)
                repo_owner, repo_name = _split_repo_full_name(repo_full_name)
                return {
                    "repo": repo_full_name,
                    "repo_owner": repo_owner,
                    "repo_name": repo_name,
                    "status": "ok",
                    "http_status": resp.status_code,
                    "request_url": url,
                    "elapsed_s": elapsed_s,
                    "attempts": attempt,
                    "jsonld_rel_path": str(rel_payload_path),
                }

            # Non-2xx: keep body snippet for debugging.
            snippet = (resp.text or "")[:4000]
            err_path = errors_dir / _jsonld_error_filename(repo_full_name, f"http_{resp.status_code}")
            err_payload = {
                "repo": repo_full_name,
                "repo_owner": _split_repo_full_name(repo_full_name)[0],
                "repo_name": _split_repo_full_name(repo_full_name)[1],
                "status": "http_error",
                "http_status": resp.status_code,
                "request_url": url,
                "body_snippet": snippet,
                "elapsed_s": elapsed_s,
                "attempts": attempt,
                "jsonld_rel_path": str(rel_payload_path),
            }
            with open(err_path, "w", encoding="utf-8") as f:
                json.dump(err_payload, f, indent=2, ensure_ascii=False)
            return err_payload

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            last_exc = e
            # Simple exponential backoff with a small cap.
            await asyncio.sleep(min(2 ** (attempt - 1), 10))
            continue

    err_path = errors_dir / _jsonld_error_filename(repo_full_name, "failed")
    err_payload = {
        "repo": repo_full_name,
        "repo_owner": _split_repo_full_name(repo_full_name)[0],
        "repo_name": _split_repo_full_name(repo_full_name)[1],
        "status": "failed",
        "error": repr(last_exc) if last_exc else "unknown error",
        "elapsed_s": None,
        "attempts": max_retries,
        "request_url": url,
        "jsonld_rel_path": str(rel_payload_path),
    }
    with open(err_path, "w", encoding="utf-8") as f:
        json.dump(err_payload, f, indent=2, ensure_ascii=False)
    return err_payload


def _crawl_repos(
    *,
    seed: str,
    rounds: int,
    cache_dir: Optional[Path],
    request_delay: float,
    max_concurrent_requests: int,
    rate_limit_buffer: int,
    crawl_dependencies: bool,
    crawl_dependents: bool,
    min_stars: int,
    max_dependents: Optional[int],
    state_file: Optional[Path],
    resume: bool,
) -> List[str]:
    tokens = _parse_github_tokens()
    client = GitHubClient(
        tokens,
        cache_dir=cache_dir,
        request_delay=request_delay,
        max_concurrent_requests=max_concurrent_requests,
        rate_limit_buffer=rate_limit_buffer,
    )
    crawler = GitHubCrawler(
        client=client,
        max_rounds=rounds,
        state_file=state_file,
        crawl_dependencies=crawl_dependencies,
        crawl_dependents=crawl_dependents,
        min_stars=min_stars,
        max_dependents=max_dependents,
    )
    # `add_seeds` accepts usernames, org/repo, or full GitHub URLs.
    crawler.add_seeds([seed])

    if resume and state_file:
        loaded = crawler.load_state()
        # If state isn't loaded, fall back to starting from the provided seed.
        if not loaded:
            crawler.add_seeds([seed])

    # Programmatic crawl defaults to showing progress, which is useful for long runs.
    crawler.crawl(show_progress=True)
    return sorted(crawler.graph.repos.keys())


async def _fetch_all_jsonld(
    *,
    repos: Iterable[str],
    api_base: str,
    jsonld_repo_segment: str,
    out_dir: Path,
    skip_existing: bool,
    concurrency: int,
    timeout_s: float,
    max_retries: int,
) -> List[Dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        tasks = [
            _fetch_one_jsonld(
                client=client,
                semaphore=semaphore,
                repo_full_name=repo,
                api_base=api_base,
                jsonld_repo_segment=jsonld_repo_segment,
                out_dir=out_dir,
                skip_existing=skip_existing,
                timeout_s=timeout_s,
                max_retries=max_retries,
            )
            for repo in repos
        ]
        return await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl one seed and fetch JSON-LD for all discovered repos.")
    parser.add_argument("--seed", required=True, help="Seed node (username, org/repo, or full GitHub URL).")
    parser.add_argument(
        "--rounds",
        type=int,
        default=2,
        help="BFS rounds. Use 2 for seed-owned repos when seed is a user/org (repos are explored in the next round).",
    )
    # `localhost` often refers to the container/WSL namespace; `host.docker.internal` is usually the right "host" gateway.
    parser.add_argument("--api-base", default="http://host.docker.internal:1234", help="Base URL for the JSON-LD API.")
    parser.add_argument("--out-dir", default="jsonld", help="Folder to store JSON-LD files.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip repo payloads that already exist under OUTDIR/jsonld/.")
    parser.add_argument("--resume-fetch", action="store_true", help="Restart fetch from previous run: only call JSON-LD for repos missing under OUTDIR/jsonld/.")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent JSON-LD requests.")
    parser.add_argument("--timeout-s", type=float, default=120.0, help="Per-request timeout (seconds).")
    parser.add_argument("--max-retries", type=int, default=3, help="Retry count for timeouts/network errors.")
    parser.add_argument("--max-repos", type=int, default=None, help="Limit repos for testing (default: no limit).")
    parser.add_argument("--crawl-dependencies", action="store_true", help="Also crawl dependency graph (SBOM).")
    parser.add_argument("--crawl-dependents", action="store_true", help="Also crawl dependents ('Used by').")
    parser.add_argument("--min-stars", type=int, default=0, help="Filter dependents/dependencies by min stars.")
    parser.add_argument("--max-dependents", type=int, default=None, help="Max dependents to fetch per repo.")
    parser.add_argument("--cache-dir", default=None, help="Optional directory for GitHub API caching.")
    parser.add_argument("--request-delay", type=float, default=0.0, help="Min delay between GitHub API requests.")
    parser.add_argument("--max-concurrent-requests", type=int, default=5, help="GitHub API concurrency.")
    parser.add_argument("--rate-limit-buffer", type=int, default=50, help="Token rate-limit safety buffer.")
    parser.add_argument("--state-file", default=None, help="Optional state file for resume/save.")
    parser.add_argument("--resume", action="store_true", help="Resume from state file (if provided).")

    args = parser.parse_args()

    jsonld_repo_segment = "gimie"

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    state_file = Path(args.state_file) if args.state_file else None

    import time

    run_started_ts = time.time()
    print(f"Starting crawl from seed={args.seed!r} for {args.rounds} round(s)...")
    repos = _crawl_repos(
        seed=args.seed,
        rounds=args.rounds,
        cache_dir=cache_dir,
        request_delay=args.request_delay,
        max_concurrent_requests=args.max_concurrent_requests,
        rate_limit_buffer=args.rate_limit_buffer,
        crawl_dependencies=args.crawl_dependencies,
        crawl_dependents=args.crawl_dependents,
        min_stars=args.min_stars,
        max_dependents=args.max_dependents,
        state_file=state_file,
        resume=args.resume,
    )

    if args.max_repos is not None:
        repos = repos[: args.max_repos]

    # Restart behavior: only call the JSON-LD API for repos without an existing payload file.
    skip_existing_effective = args.skip_existing or args.resume_fetch
    if args.resume_fetch:
        jsonld_dir = out_dir / "jsonld"
        jsonld_dir.mkdir(parents=True, exist_ok=True)
        repos = [repo for repo in repos if not (jsonld_dir / _jsonld_payload_filename(repo)).exists()]

    print(f"Discovered {len(repos)} repo(s). Fetching JSON-LD...")

    results = asyncio.run(
        _fetch_all_jsonld(
            repos=repos,
            api_base=args.api_base,
            jsonld_repo_segment=jsonld_repo_segment,
            out_dir=out_dir,
            skip_existing=skip_existing_effective,
            concurrency=args.concurrency,
            timeout_s=args.timeout_s,
            max_retries=args.max_retries,
        )
    )

    ok = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = len(results) - ok - skipped
    print(f"JSON-LD done: ok={ok}, skipped={skipped}, failed/other={failed}.")

    run_elapsed_s = time.time() - run_started_ts

    skipped_repos = [r.get("repo") for r in results if r.get("status") == "skipped" and r.get("repo")]
    failed_repos = [
        r.get("repo")
        for r in results
        if r.get("status") not in ("ok", "skipped") and r.get("repo")
    ]

    manifest_path = out_dir / "run_metadata.json"

    run_entry = {
        "seed": args.seed,
        "rounds": args.rounds,
        "api_base": args.api_base,
        "jsonld_requests_include_force_refresh": True,
        "skip_existing_requested": args.skip_existing,
        "skip_existing_effective": skip_existing_effective,
        "concurrency": args.concurrency,
        "timeout_s": args.timeout_s,
        "max_retries": args.max_retries,
        "run_started_unix": run_started_ts,
        "run_elapsed_s": run_elapsed_s,
        "discovered_repos_count": len(repos),
        "discovered_repos_sample": repos[:50],
        "summary": {"ok": ok, "skipped": skipped, "failed_or_other": failed},
        "skipped_repos_sample": skipped_repos[:50],
        "failed_repos_sample": failed_repos[:50],
        "results": results,
        "output_layout": {
            "jsonld_dir": "jsonld/",
            "errors_dir": "jsonld_errors/",
            "jsonld_payload_filename_basis": "owner__repo.json (owner and repo are sanitized filesystem components)",
        },
    }

    # Append runs into the same metadatafile, but keep backwards compatibility:
    # also copy the latest run entry to top-level keys.
    manifest_data: Dict[str, Any]
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
        except Exception:
            manifest_data = {}
    else:
        manifest_data = {}

    runs_list = manifest_data.get("runs")
    if not isinstance(runs_list, list):
        runs_list = []
        if manifest_data:
            # Older single-run format: treat existing content as the "runs" entry.
            runs_list.append(manifest_data)

    runs_list.append(run_entry)
    manifest_data["runs"] = runs_list
    manifest_data["runs_count"] = len(runs_list)

    for k, v in run_entry.items():
        manifest_data[k] = v

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    print(f"Wrote run metadata to {manifest_path}")


if __name__ == "__main__":
    main()

