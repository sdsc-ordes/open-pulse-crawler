"""Test to verify concurrency issues with the crawler."""

import time
from pathlib import Path
from src.open_pulse_crawler.github_client import GitHubClient
from src.open_pulse_crawler.crawler import GitHubCrawler
import os

def test_sequential_vs_concurrent():
    """Demonstrate that crawler processes nodes sequentially, not concurrently."""
    
    # Get token
    tokens = os.getenv('GITHUB_TOKEN', '').split(',')
    if not tokens or not tokens[0]:
        print("❌ No GitHub token found. Set GITHUB_TOKEN environment variable.")
        return
    
    # Test with a small delay to simulate API latency
    client = GitHubClient(
        tokens=tokens,
        cache_dir=Path('./test_concurrency_cache'),
        request_delay=0.1,  # 100ms delay per request
        max_concurrent_requests=5,  # Should allow 5 concurrent
        rate_limit_buffer=100
    )
    
    crawler = GitHubCrawler(
        client=client,
        max_rounds=1
    )
    
    # Add 10 test seeds (orgs that should exist)
    test_seeds = [
        'pytorch',
        'tensorflow',
        'kubernetes',
        'docker',
        'microsoft',
        'google',
        'facebook',
        'apple',
        'amazon',
        'netflix'
    ]
    
    print("\n" + "="*60)
    print("CONCURRENCY TEST")
    print("="*60)
    print(f"\nConfiguration:")
    print(f"  - Nodes to process: {len(test_seeds)}")
    print(f"  - Request delay: 0.1s")
    print(f"  - Max concurrent: 5")
    print(f"\nExpected time if SEQUENTIAL: ~{len(test_seeds) * 0.1}s")
    print(f"Expected time if CONCURRENT (5 parallel): ~{(len(test_seeds) / 5) * 0.1}s")
    print()
    
    crawler.add_seeds(test_seeds)
    
    start = time.time()
    crawler.crawl(show_progress=False)
    elapsed = time.time() - start
    
    print(f"\n" + "="*60)
    print(f"RESULT: {elapsed:.2f}s elapsed")
    print(f"Time per node: {elapsed / len(test_seeds):.2f}s")
    
    if elapsed > 0.8:  # Should be ~0.2s if concurrent (5 parallel batches)
        print(f"\n⚠️  PROBLEM DETECTED: Processing is SEQUENTIAL, not concurrent!")
        print(f"   With {len(test_seeds)} nodes and 5 concurrent slots,")
        print(f"   this should take ~0.2s, but took {elapsed:.2f}s")
    else:
        print(f"\n✅ Processing appears to be concurrent")
    
    print("="*60 + "\n")
    
    # Cleanup
    import shutil
    if Path('./test_concurrency_cache').exists():
        shutil.rmtree('./test_concurrency_cache')


if __name__ == '__main__':
    test_sequential_vs_concurrent()
