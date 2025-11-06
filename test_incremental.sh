#!/bin/bash
# Quick test of incremental export feature

# Clean up test directory
rm -rf /tmp/test_incremental_output

# Create a simple test seed
echo "caviri" > /tmp/test_seeds.txt

# Run crawler with incremental export for 2 rounds
echo "Testing incremental export with 2 rounds..."
open-pulse-crawler crawl \
  --seed-file /tmp/test_seeds.txt \
  --rounds 2 \
  --output-dir /tmp/test_incremental_output \
  --cache-dir /workspaces/open-pulse-crawler/data/deeplabcut/cache \
  --incremental-export \
  --no-csv \
  --verbose

# Check results
echo ""
echo "Checking output structure..."
tree -L 2 /tmp/test_incremental_output || ls -lR /tmp/test_incremental_output

echo ""
echo "Test completed!"
