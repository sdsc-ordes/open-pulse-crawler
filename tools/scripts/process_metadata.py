#!/usr/bin/env python3

import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Set, Optional, List, Tuple
import aiohttp
import typer

app = typer.Typer()


class MetadataProcessor:
    """Download and process metadata from CSV with parallel requests."""
    
    def __init__(self, base_url: str, output_dir: str, delay: float = 1.0, max_concurrent: int = 10, failed_output_dir: Optional[str] = None, force_refresh: bool = False, timeout: float = 300.0):
        self.base_url = base_url.rstrip('/')
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.max_concurrent = max_concurrent
        self.force_refresh = force_refresh
        self.timeout = timeout
        self.cache: Set[str] = set()
        # Snapshot of cache items present before this run
        self.cache_initial: Set[str] = set()
        # Items successfully downloaded in this run (not served from cache)
        self.downloaded_this_run: Set[str] = set()
        self.failed_items: Set[str] = set()
        self.failed_output_dir = Path(failed_output_dir) if failed_output_dir else self.output_dir.parent / "failed-items-properties"
        self.failed_output_dir.mkdir(parents=True, exist_ok=True)
        self._load_cache()
    
    def _load_cache(self):
        """Load list of already downloaded items."""
        for file in self.output_dir.glob("*.json"):
            item_name = file.stem.replace("_", "/", 1)
            self.cache.add(item_name)
        # Preserve initial cache snapshot (to distinguish pre-existing cache from new downloads)
        self.cache_initial = set(self.cache)
        
        cache_msg = f"📦 Found {len(self.cache)} cached items"
        
        # Also check for failed items to report
        failed_count = 0
        for file in self.failed_output_dir.glob("*.json"):
            failed_count += 1
        
        if self.cache:
            typer.echo(cache_msg)
        if failed_count > 0:
            typer.echo(f"⚠️  Found {failed_count} previously failed items (will be retried if in scope)")
    
    def _get_api_endpoint(self, item: str, item_type: str) -> str:
        """Construct API endpoint based on item type."""
        github_url = f"https://github.com/{item}"
        
        # Base query parameters
        force_param = "&force_refresh=true" if self.force_refresh else ""
        
        if item_type == "user":
            return f"{self.base_url}/v1/user/llm/json/{github_url}?enrich_orgs=true&enrich_users=true{force_param}"
        elif item_type == "repo":
            return f"{self.base_url}/v1/repository/llm/json/{github_url}?enrich_orgs=true&enrich_users=true{force_param}"
        elif item_type == "org":
            return f"{self.base_url}/v1/org/llm/json/{github_url}?enrich_orgs=true{force_param}"
        else:
            raise ValueError(f"Unknown item type: {item_type}")
    
    def _sanitize_filename(self, item: str) -> str:
        """Convert item name to safe filename."""
        return item.replace("/", "_").replace("\\", "_")
    
    async def download_metadata(self, session: aiohttp.ClientSession, item: str, item_type: str, semaphore: asyncio.Semaphore, max_retries: int = 3) -> bool:
        """Download metadata for a single item asynchronously with retry logic."""
        if item in self.cache:
            return True
        if item in self.failed_items:
            return False
        
        async with semaphore:
            endpoint = self._get_api_endpoint(item, item_type)
            last_error = None
            attempt = 0
            
            for attempt in range(1, max_retries + 1):
                try:
                    if attempt == 1:
                        typer.echo(f"⬇️  Downloading: {item} ({item_type})")
                    else:
                        typer.echo(f"🔄 Retry {attempt}/{max_retries}: {item} ({item_type})")
                    
                    timeout = aiohttp.ClientTimeout(total=self.timeout)
                    async with session.get(endpoint, timeout=timeout) as response:
                        response.raise_for_status()
                        data = await response.json()
                    
                    # Save to file
                    filename = self._sanitize_filename(item) + ".json"
                    filepath = self.output_dir / filename
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    self.cache.add(item)
                    self.downloaded_this_run.add(item)
                    
                    # Remove from failed items directory if it exists there (successful retry)
                    failed_filepath = self.failed_output_dir / filename
                    if failed_filepath.exists():
                        failed_filepath.unlink()
                        typer.echo(f"🗑️  Removed from failed items: {failed_filepath}")
                    
                    if attempt > 1:
                        typer.echo(f"✅ Saved (after {attempt} attempts): {filepath}")
                    else:
                        typer.echo(f"✅ Saved: {filepath}")
                    
                    if self.delay > 0:
                        await asyncio.sleep(self.delay)
                    return True
                    
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_error = e
                    error_msg = str(e) if str(e) else f"{type(e).__name__}: {repr(e)}"
                    
                    if attempt < max_retries:
                        # Wait before retrying (exponential backoff)
                        wait_time = min(2 ** attempt, 10)  # Max 10 seconds
                        typer.echo(f"⚠️  Attempt {attempt} failed: {error_msg}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        typer.echo(f"❌ Failed after {max_retries} attempts: {item}: {error_msg}")
                        
                except Exception as e:
                    last_error = e
                    error_msg = str(e) if str(e) else f"{type(e).__name__}: {repr(e)}"
                    typer.echo(f"❌ Error processing {item}: {error_msg}")
                    break  # Don't retry on non-network errors
            
            # All retries failed - save error info
            self.failed_items.add(item)
            filename = self._sanitize_filename(item) + ".json"
            failed_filepath = self.failed_output_dir / filename
            error_msg = str(last_error) if str(last_error) else f"{type(last_error).__name__}: {repr(last_error)}"
            error_data = {
                "item": item,
                "item_type": item_type,
                "error": error_msg,
                "error_type": type(last_error).__name__,
                "endpoint": endpoint,
                "attempts": attempt
            }
            with open(failed_filepath, 'w', encoding='utf-8') as f:
                json.dump(error_data, f, indent=2, ensure_ascii=False)
            typer.echo(f"❌ Saved failed item: {failed_filepath}")
            return False
    
    async def download_all_metadata(self, items_to_process: Dict[str, str]) -> Tuple[int, int]:
        """Download metadata for all items in parallel."""
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async with aiohttp.ClientSession() as session:
            tasks = [
                self.download_metadata(session, item, item_type, semaphore)
                for item, item_type in items_to_process.items()
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=False)
        
        success_count = sum(1 for r in results if r)
        failed_count = len(results) - success_count
        
        return success_count, failed_count
    
    def extract_affiliation(self, item: str) -> Tuple[Optional[str], bool]:
        """Extract all affiliations from JSON metadata and get relatedToEPFL field.
        
        Returns:
            Tuple of (affiliations_string, is_epfl) where:
            - affiliations_string: All affiliations joined by '//'
            - is_epfl: Value from the 'relatedToEPFL' field in the JSON
        """
        filename = self._sanitize_filename(item) + ".json"
        filepath = self.output_dir / filename
        
        if not filepath.exists():
            return None, False
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if 'output' in data:
                output = data['output']
                
                # Extract affiliations (only string values, ignore dict objects)
                affiliations_str = None
                if 'relatedToOrganization' in output:
                    orgs = output['relatedToOrganization']
                    if isinstance(orgs, list) and len(orgs) > 0:
                        # Filter to only include string values, ignore dict objects
                        org_strings = [org for org in orgs if isinstance(org, str)]
                        if org_strings:
                            # Join all string affiliations with //
                            affiliations_str = ' // '.join(org_strings)
                
                # Extract relatedToEPFL field (default to False if not present)
                is_epfl = output.get('relatedToEPFL', False)
                
                return affiliations_str, is_epfl
            
            return None, False
            
        except Exception as e:
            typer.echo(f"⚠️  Error reading {filepath}: {str(e)}")
            return None, False

    def extract_stats(self, item: str) -> Dict[str, float]:
        """Extract token and timing stats from the item's JSON metadata.

        Returns a dict with numeric totals (defaults to 0 if missing):
        - agent_input_tokens, agent_output_tokens, total_tokens
        - estimated_input_tokens, estimated_output_tokens, estimated_total_tokens
        - duration (seconds)
        - status_code (int, 0 if missing)
        """
        filename = self._sanitize_filename(item) + ".json"
        filepath = self.output_dir / filename

        defaults = {
            'agent_input_tokens': 0,
            'agent_output_tokens': 0,
            'total_tokens': 0,
            'estimated_input_tokens': 0,
            'estimated_output_tokens': 0,
            'estimated_total_tokens': 0,
            'duration': 0.0,
            'status_code': 0,
        }

        if not filepath.exists():
            return defaults.copy()

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            stats = data.get('stats', {}) or {}
            out = defaults.copy()
            out.update({
                'agent_input_tokens': stats.get('agent_input_tokens', 0) or 0,
                'agent_output_tokens': stats.get('agent_output_tokens', 0) or 0,
                'total_tokens': stats.get('total_tokens', 0) or 0,
                'estimated_input_tokens': stats.get('estimated_input_tokens', 0) or 0,
                'estimated_output_tokens': stats.get('estimated_output_tokens', 0) or 0,
                'estimated_total_tokens': stats.get('estimated_total_tokens', 0) or 0,
                'duration': stats.get('duration', 0.0) or 0.0,
                'status_code': stats.get('status_code', 0) or 0,
            })
            return out
        except Exception as e:
            typer.echo(f"⚠️  Error reading stats from {filepath}: {str(e)}")
            return defaults.copy()


@app.command()
def process(
    csv_path: str = typer.Argument(..., help="Path to the edges CSV file"),
    output_dir: str = typer.Option("metadata", "--output-dir", "-o", help="Output directory for JSON files"),
    failed_output_dir: str = typer.Option(None, "--failed-output-dir", help="Directory for failed metadata downloads"),
    affiliations_csv: str = typer.Option("affiliations.csv", "--affiliations", "-a", help="Output CSV for affiliations"),
    api_url: str = typer.Option("http://imagingplazadev.epfl.ch:7511", "--api-url", help="Base API URL"),
    delay: float = typer.Option(1.0, "--delay", help="Delay between API calls in seconds"),
    max_concurrent: int = typer.Option(10, "--max-concurrent", "-c", help="Maximum number of concurrent requests"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit number of items to process (for testing)"),
    token_limit: Optional[int] = typer.Option(None, "--token-limit", "-t", help="Stop processing when estimated total tokens exceed this limit"),
    only_users: bool = typer.Option(False, "--only-users", help="Process only user entities"),
    only_orgs: bool = typer.Option(False, "--only-orgs", help="Process only organization entities"),
    only_repos: bool = typer.Option(False, "--only-repos", help="Process only repository entities"),
    force_refresh: bool = typer.Option(False, "--force-refresh", help="Add force_refresh=true to API endpoints to bypass cache"),
    timeout: float = typer.Option(600.0, "--timeout", help="Request timeout in seconds (default: 600s / 10 minutes)"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Process only previously failed items"),
):
    """
    Download metadata and extract affiliations from CSV.
    
    Example:
        python process_metadata.py edges.csv --output-dir metadata --affiliations affiliations.csv
    """
    typer.echo(f"🚀 Starting metadata processing from {csv_path}")
    
    # Check if CSV exists
    if not os.path.exists(csv_path):
        typer.echo(f"❌ Error: CSV file {csv_path} not found")
        sys.exit(1)
    
    # Initialize processor
    processor = MetadataProcessor(api_url, output_dir, delay, max_concurrent, failed_output_dir, force_refresh, timeout)
    
    typer.echo(f"⏱️  Request timeout set to {timeout} seconds ({timeout/60:.1f} minutes)")
    if force_refresh:
        typer.echo("🔄 Force refresh enabled - API will bypass cache")
    
    # Determine allowed entity types based on filters
    allowed_types = set()
    if only_users:
        allowed_types.add('user')
    if only_orgs:
        allowed_types.add('org')
    if only_repos:
        allowed_types.add('repo')
    
    # If no filters specified, allow all types
    if not allowed_types:
        allowed_types = {'user', 'org', 'repo'}
    
    # Step 1: Collect unique items and download metadata
    typer.echo("\n📥 Step 1: Downloading metadata...")
    if allowed_types != {'user', 'org', 'repo'}:
        typer.echo(f"🔍 Filtering for entity types: {', '.join(sorted(allowed_types))}")
    typer.echo(f"⚡ Using {max_concurrent} concurrent requests")
    items_to_process: Dict[str, str] = {}
    
    try:
        # If retry_failed flag is set, load only failed items
        if retry_failed:
            typer.echo("🔄 Retry mode: Processing only previously failed items")
            for file in processor.failed_output_dir.glob("*.json"):
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        failed_data = json.load(f)
                        item = failed_data.get('item')
                        item_type = failed_data.get('item_type')
                        if item and item_type and item_type in allowed_types:
                            items_to_process[item] = item_type
                except Exception as e:
                    typer.echo(f"⚠️  Could not read failed item {file}: {e}")
        else:
            # Normal mode: load from CSV
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    source = row.get('source', '').strip()
                    target = row.get('target', '').strip()
                    source_type = row.get('source_type', '').strip()
                    target_type = row.get('target_type', '').strip()
                    
                    # Only process if type is allowed
                    if source and source_type in allowed_types:
                        items_to_process[source] = source_type
                    
                    if target and target_type in allowed_types:
                        items_to_process[target] = target_type
        
        # Sort items by priority: org > user > repo
        # This ensures we process orgs first, then users, then repos
        type_priority = {'org': 0, 'user': 1, 'repo': 2}
        items_sorted = sorted(
            items_to_process.items(),
            key=lambda x: (type_priority.get(x[1], 999), x[0])
        )
        items_to_process = dict(items_sorted)
        
        # Apply limit if specified (after sorting, so we get orgs/users first)
        if limit and len(items_to_process) > limit:
            typer.echo(f"⚠️  Limiting to first {limit} items (found {len(items_to_process)} total)")
            items_to_process = dict(list(items_to_process.items())[:limit])
        
        # Apply token limit if specified (estimate based on cached items first)
        # Items are already sorted by priority (org > user > repo)
        if token_limit:
            typer.echo(f"⚠️  Token limit enabled: {token_limit} estimated_total_tokens")
            cumulative_tokens = 0
            limited_items = {}
            
            for item, item_type in items_to_process.items():
                # Check if item is cached - if so, we can read its token stats
                if item in processor.cache_initial:
                    stats = processor.extract_stats(item)
                    estimated = int(stats.get('estimated_total_tokens', 0) or 0)
                else:
                    # Not cached - estimate conservatively (use average or fixed estimate)
                    # Using a conservative estimate of ~35000 tokens per item (based on the sample you showed)
                    estimated = 35000
                
                if cumulative_tokens + estimated <= token_limit:
                    limited_items[item] = item_type
                    cumulative_tokens += estimated
                else:
                    typer.echo(f"⚠️  Stopping at {len(limited_items)} items (estimated tokens: {cumulative_tokens}, limit: {token_limit})")
                    break
            
            items_to_process = limited_items
        
        # Display entity type breakdown
        type_counts = {}
        for item_type in items_to_process.values():
            type_counts[item_type] = type_counts.get(item_type, 0) + 1
        
        typer.echo(f"📊 Found {len(items_to_process)} unique items to process")
        if type_counts:
            breakdown = ', '.join([f"{count} {entity_type}(s)" for entity_type, count in sorted(type_counts.items())])
            typer.echo(f"   Entity breakdown: {breakdown}")
        
        # Download metadata in parallel
        start_time = time.time()
        success_count, failed_count = asyncio.run(processor.download_all_metadata(items_to_process))
        elapsed_time = time.time() - start_time
        
        typer.echo(f"\n📊 Download Summary:")
        typer.echo(f"   ⏱️  Time elapsed: {elapsed_time:.2f} seconds")
        typer.echo(f"   ✅ Successfully downloaded: {success_count}")
        typer.echo(f"   ❌ Failed: {failed_count}")
        typer.echo(f"   📦 Total cached: {len(processor.cache)}")
        
        # Step 2: Extract affiliations
        typer.echo(f"\n🔍 Step 2: Extracting affiliations...")
        
        results = []
        token_totals = {
            'agent_input_tokens': 0,
            'agent_output_tokens': 0,
            'total_tokens': 0,
            'estimated_input_tokens': 0,
            'estimated_output_tokens': 0,
            'estimated_total_tokens': 0,
            'duration': 0.0,
            'items_with_status_200': 0,
            'items_counted': 0,
        }
        with_affiliation = 0
        with_epfl = 0
        
        # Track counts by entity type
        entity_type_stats = {
            'user': {'total': 0, 'with_affiliation': 0, 'with_epfl': 0},
            'org': {'total': 0, 'with_affiliation': 0, 'with_epfl': 0},
            'repo': {'total': 0, 'with_affiliation': 0, 'with_epfl': 0},
        }
        
        for item in items_to_process.keys():
            item_type = items_to_process[item]
            entity_type_stats[item_type]['total'] += 1
            
            affiliation, is_epfl = processor.extract_affiliation(item)
            stats = processor.extract_stats(item)
            
            if affiliation:
                with_affiliation += 1
                entity_type_stats[item_type]['with_affiliation'] += 1
                epfl_marker = "🇨🇭 " if is_epfl else ""
                typer.echo(f"✅ {epfl_marker}{item} -> {affiliation}")
                if is_epfl:
                    with_epfl += 1
                    entity_type_stats[item_type]['with_epfl'] += 1
            
            results.append({
                'item': item,
                'relatedToOrganization': affiliation or '',
                'relatedToEPFL': is_epfl
            })

            # Accumulate token stats ONLY for items actually downloaded in this run (exclude cache hits)
            if item in processor.downloaded_this_run:
                token_totals['agent_input_tokens'] += int(stats.get('agent_input_tokens', 0) or 0)
                token_totals['agent_output_tokens'] += int(stats.get('agent_output_tokens', 0) or 0)
                token_totals['total_tokens'] += int(stats.get('total_tokens', 0) or 0)
                token_totals['estimated_input_tokens'] += int(stats.get('estimated_input_tokens', 0) or 0)
                token_totals['estimated_output_tokens'] += int(stats.get('estimated_output_tokens', 0) or 0)
                token_totals['estimated_total_tokens'] += int(stats.get('estimated_total_tokens', 0) or 0)
                token_totals['duration'] += float(stats.get('duration', 0.0) or 0.0)
                token_totals['items_counted'] += 1
                if int(stats.get('status_code', 0) or 0) == 200:
                    token_totals['items_with_status_200'] += 1
        
        # Write affiliations CSV
        with open(affiliations_csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(
                f,
                fieldnames=['item', 'relatedToOrganization', 'relatedToEPFL'],
                delimiter=';'
            )
            writer.writeheader()
            writer.writerows(results)
        
        typer.echo(f"\n📊 Affiliation Summary:")
        typer.echo(f"   📝 Total items: {len(items_to_process)}")
        typer.echo(f"   ✅ Items with affiliation: {with_affiliation}")
        typer.echo(f"   🇨🇭 Items related to EPFL: {with_epfl}")
        
        # Display breakdown by entity type
        for entity_type in sorted(entity_type_stats.keys()):
            stats = entity_type_stats[entity_type]
            if stats['total'] > 0:
                typer.echo(f"   {entity_type.upper()}: {stats['total']} total, {stats['with_affiliation']} with affiliation, {stats['with_epfl']} EPFL-related")
        
        typer.echo(f"   📁 Metadata directory: {output_dir}")
        typer.echo(f"   📄 Affiliations CSV: {affiliations_csv}")

        # Token statistics summary (console)
        token_limit_reached = token_limit and token_totals['estimated_total_tokens'] >= token_limit
        
        typer.echo(f"\n📈 Token Usage Summary (non-cache only):")
        typer.echo(f"   • agent_input_tokens: {token_totals['agent_input_tokens']}")
        typer.echo(f"   • agent_output_tokens: {token_totals['agent_output_tokens']}")
        typer.echo(f"   • total_tokens: {token_totals['total_tokens']}")
        typer.echo(f"   • estimated_input_tokens: {token_totals['estimated_input_tokens']}")
        typer.echo(f"   • estimated_output_tokens: {token_totals['estimated_output_tokens']}")
        typer.echo(f"   • estimated_total_tokens: {token_totals['estimated_total_tokens']}")
        typer.echo(f"   • total_duration_seconds: {token_totals['duration']:.3f}")
        typer.echo(f"   • items_with_status_200: {token_totals['items_with_status_200']} / {token_totals['items_counted']}")
        if token_limit:
            typer.echo(f"   • token_limit: {token_limit} ({'⚠️ REACHED' if token_limit_reached else '✅ within limit'})")

        # Write token summary CSV next to affiliations CSV
        stats_csv = affiliations_csv.replace('.csv', '_stats.csv') if affiliations_csv.endswith('.csv') else affiliations_csv + '_stats.csv'
        with open(stats_csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    'items_total',
                    'items_processed',
                    'items_with_status_200',
                    'agent_input_tokens',
                    'agent_output_tokens',
                    'total_tokens',
                    'estimated_input_tokens',
                    'estimated_output_tokens',
                    'estimated_total_tokens',
                    'total_duration_seconds'
                ],
                delimiter=';'
            )
            writer.writeheader()
            writer.writerow({
                'items_total': len(items_to_process),
                'items_processed': token_totals['items_counted'],
                'items_with_status_200': token_totals['items_with_status_200'],
                'agent_input_tokens': token_totals['agent_input_tokens'],
                'agent_output_tokens': token_totals['agent_output_tokens'],
                'total_tokens': token_totals['total_tokens'],
                'estimated_input_tokens': token_totals['estimated_input_tokens'],
                'estimated_output_tokens': token_totals['estimated_output_tokens'],
                'estimated_total_tokens': token_totals['estimated_total_tokens'],
                'total_duration_seconds': f"{token_totals['duration']:.3f}"
            })
        typer.echo(f"   📄 Token Stats CSV: {stats_csv}")
        
    except Exception as e:
        typer.echo(f"❌ Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    app()

# Using the combined script with parallel requests
# python3 tools/scripts/process_metadata.py data/sdsc/output/edges_20251003_115511.csv \
#   --output-dir data/sdsc/items-properties \
#   --affiliations data/sdsc/affiliations.csv \
#   --api-url http://git-metadata-extractor:1235 \
#   --delay 0.1 \
#   --max-concurrent 5 \
#   --limit 100

# Entity type filtering examples:
# Process only users:
#   --only-users
# Process only organizations:
#   --only-orgs
# Process only repositories:
#   --only-repos
# Process users and orgs (skip repos):
#   --only-users --only-orgs
