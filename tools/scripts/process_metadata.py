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
    
    def __init__(self, base_url: str, output_dir: str, delay: float = 1.0, max_concurrent: int = 10):
        self.base_url = base_url.rstrip('/')
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.max_concurrent = max_concurrent
        self.cache: Set[str] = set()
        self.failed_items: Set[str] = set()
        self._load_cache()
    
    def _load_cache(self):
        """Load list of already downloaded items."""
        for file in self.output_dir.glob("*.json"):
            item_name = file.stem.replace("_", "/", 1)
            self.cache.add(item_name)
        if self.cache:
            typer.echo(f"📦 Found {len(self.cache)} cached items")
    
    def _get_api_endpoint(self, item: str, item_type: str) -> str:
        """Construct API endpoint based on item type."""
        github_url = f"https://github.com/{item}"
        
        if item_type == "user":
            return f"{self.base_url}/v1/user/llm/json/{github_url}?enrich_orgs=true"
        elif item_type == "repo":
            return f"{self.base_url}/v1/repository/llm/json/{github_url}?enrich_orgs=true"
        elif item_type == "org":
            return f"{self.base_url}/v1/org/llm/json/{github_url}?enrich_orgs=true"
        else:
            raise ValueError(f"Unknown item type: {item_type}")
    
    def _sanitize_filename(self, item: str) -> str:
        """Convert item name to safe filename."""
        return item.replace("/", "_").replace("\\", "_")
    
    async def download_metadata(self, session: aiohttp.ClientSession, item: str, item_type: str, semaphore: asyncio.Semaphore) -> bool:
        """Download metadata for a single item asynchronously."""
        if item in self.cache:
            return True
        
        if item in self.failed_items:
            return False
        
        async with semaphore:
            try:
                endpoint = self._get_api_endpoint(item, item_type)
                typer.echo(f"⬇️  Downloading: {item} ({item_type})")
                
                timeout = aiohttp.ClientTimeout(total=300)
                async with session.get(endpoint, timeout=timeout) as response:
                    response.raise_for_status()
                    data = await response.json()
                
                # Save to file
                filename = self._sanitize_filename(item) + ".json"
                filepath = self.output_dir / filename
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                
                self.cache.add(item)
                typer.echo(f"✅ Saved: {filepath}")
                
                # Add delay between requests to avoid overwhelming the server
                if self.delay > 0:
                    await asyncio.sleep(self.delay)
                return True
                
            except aiohttp.ClientError as e:
                typer.echo(f"❌ Failed to download {item}: {str(e)}")
                self.failed_items.add(item)
                return False
            except Exception as e:
                typer.echo(f"❌ Error processing {item}: {str(e)}")
                self.failed_items.add(item)
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
                
                # Extract affiliations
                affiliations_str = None
                if 'relatedToOrganization' in output:
                    orgs = output['relatedToOrganization']
                    if isinstance(orgs, list) and len(orgs) > 0:
                        # Join all affiliations with //
                        affiliations_str = ' // '.join(orgs)
                
                # Extract relatedToEPFL field (default to False if not present)
                is_epfl = output.get('relatedToEPFL', False)
                
                return affiliations_str, is_epfl
            
            return None, False
            
        except Exception as e:
            typer.echo(f"⚠️  Error reading {filepath}: {str(e)}")
            return None, False


@app.command()
def process(
    csv_path: str = typer.Argument(..., help="Path to the edges CSV file"),
    output_dir: str = typer.Option("metadata", "--output-dir", "-o", help="Output directory for JSON files"),
    affiliations_csv: str = typer.Option("affiliations.csv", "--affiliations", "-a", help="Output CSV for affiliations"),
    api_url: str = typer.Option("http://imagingplazadev.epfl.ch:7511", "--api-url", help="Base API URL"),
    delay: float = typer.Option(1.0, "--delay", help="Delay between API calls in seconds"),
    max_concurrent: int = typer.Option(10, "--max-concurrent", "-c", help="Maximum number of concurrent requests"),
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
    processor = MetadataProcessor(api_url, output_dir, delay, max_concurrent)
    
    # Step 1: Collect unique items and download metadata
    typer.echo("\n📥 Step 1: Downloading metadata...")
    typer.echo(f"⚡ Using {max_concurrent} concurrent requests")
    items_to_process: Dict[str, str] = {}
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                source = row.get('source', '').strip()
                target = row.get('target', '').strip()
                source_type = row.get('source_type', '').strip()
                target_type = row.get('target_type', '').strip()
                
                if source and source_type in ['user', 'repo', 'org']:
                    items_to_process[source] = source_type
                
                if target and target_type in ['user', 'repo', 'org']:
                    items_to_process[target] = target_type
        
        typer.echo(f"📊 Found {len(items_to_process)} unique items to process")
        
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
        with_affiliation = 0
        with_epfl = 0
        
        for item in items_to_process.keys():
            affiliation, is_epfl = processor.extract_affiliation(item)
            
            if affiliation:
                with_affiliation += 1
                epfl_marker = "🇨🇭 " if is_epfl else ""
                typer.echo(f"✅ {epfl_marker}{item} -> {affiliation}")
                if is_epfl:
                    with_epfl += 1
            
            results.append({
                'item': item,
                'relatedToOrganization': affiliation or '',
                'relatedToEPFL': is_epfl
            })
        
        # Write affiliations CSV
        with open(affiliations_csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['item', 'relatedToOrganization', 'relatedToEPFL'])
            writer.writeheader()
            writer.writerows(results)
        
        typer.echo(f"\n📊 Affiliation Summary:")
        typer.echo(f"   📝 Total items: {len(items_to_process)}")
        typer.echo(f"   ✅ Items with affiliation: {with_affiliation}")
        typer.echo(f"   🇨🇭 Items related to EPFL: {with_epfl}")
        typer.echo(f"   📁 Metadata directory: {output_dir}")
        typer.echo(f"   📄 Affiliations CSV: {affiliations_csv}")
        
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
#   --max-concurrent 5