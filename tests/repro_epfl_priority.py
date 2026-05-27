
import os
import csv
import json
import sys
from pathlib import Path
import shutil
from typer.testing import CliRunner
from importlib.util import spec_from_file_location, module_from_spec

# Add tools/scripts to path to import process_metadata
sys.path.append(os.path.abspath("tools/scripts"))

# Import the module dynamically since it's a script
spec = spec_from_file_location("process_metadata", "tools/scripts/process_metadata.py")
process_metadata = module_from_spec(spec)
spec.loader.exec_module(process_metadata)

runner = CliRunner()

def test_epfl_prioritization():
    # Setup temporary directories
    base_dir = Path("tests/temp_epfl_test")
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True)
    
    output_dir = base_dir / "metadata"
    output_dir.mkdir()
    
    csv_path = base_dir / "edges.csv"
    affiliations_csv = base_dir / "affiliations.csv"
    
    # 1. Create dummy cached items
    # User A: EPFL related
    user_a_data = {
        "output": {
            "relatedToEPFL": True,
            "relatedToOrganization": ["EPFL"]
        }
    }
    with open(output_dir / "UserA.json", "w") as f:
        json.dump(user_a_data, f)
        
    # User B: Not EPFL related
    user_b_data = {
        "output": {
            "relatedToEPFL": False,
            "relatedToOrganization": ["Other"]
        }
    }
    with open(output_dir / "UserB.json", "w") as f:
        json.dump(user_b_data, f)

    # 2. Create edges CSV
    # Repo1 connected to UserA (EPFL) -> Should be prioritized
    # Repo2 connected to UserB (Non-EPFL) -> Should be normal priority
    # Repo3 connected to nothing -> Normal priority
    
    edges = [
        {"source": "UserA", "source_type": "user", "target": "Repo1", "target_type": "repo"},
        {"source": "UserB", "source_type": "user", "target": "Repo2", "target_type": "repo"},
        {"source": "UserC", "source_type": "user", "target": "Repo3", "target_type": "repo"},
    ]
    
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "source_type", "target", "target_type"])
        writer.writeheader()
        writer.writerows(edges)
        
    print("Running process_metadata with --prioritize-epfl...")
    
    # We mock the download_all_metadata to avoid actual network calls and just check order
    original_download = process_metadata.MetadataProcessor.download_all_metadata
    
    download_order = []
    
    async def mock_download(self, items_to_process):
        print("Mock download called with items:", list(items_to_process.keys()))
        download_order.extend(list(items_to_process.keys()))
        return len(items_to_process), 0
        
    process_metadata.MetadataProcessor.download_all_metadata = mock_download
    
    try:
        result = runner.invoke(process_metadata.app, [
            str(csv_path),
            "--output-dir", str(output_dir),
            "--affiliations", str(affiliations_csv),
            "--prioritize-epfl",
            "--limit", "10",
            "--max-concurrent", "1" # Force sequential to check order easily if it wasn't mocked
        ])
        
        print("Exit code:", result.exit_code)
        if result.exit_code != 0:
            print(result.stdout)
            
        # Check output
        print("Download order:", download_order)
        
        # Expected: UserA (cached), UserB (cached), UserC (new), Repo1 (EPFL related), Repo2, Repo3
        # Wait, the script filters out cached items if --skip-cached is used. We didn't use it.
        # But the script sorts ALL items.
        # UserA is EPFL.
        # Repo1 is connected to UserA.
        # So Repo1 should be prioritized over Repo2 and Repo3.
        # UserC is new.
        
        # The sort key is: (priority_group, type_priority, name)
        # priority_group: 0 if EPFL related, 1 otherwise.
        # type_priority: org=0, user=1, repo=2
        
        # EPFL Related Items:
        # UserA is in cache and is EPFL.
        # Repo1 is connected to UserA. -> EPFL Related.
        
        # So Repo1 should have priority_group 0.
        # UserA (if processed) -> priority_group 0 (it is connected to itself? No, logic says if source in cached_epfl, prioritize target. If target in cached_epfl, prioritize source.)
        # Wait, does UserA get prioritized?
        # Logic:
        # if source in cached_epfl and target in items_to_process: epfl_related_items.add(target)
        # if target in cached_epfl and source in items_to_process: epfl_related_items.add(source)
        
        # UserA is source. Target is Repo1.
        # UserA is in cached_epfl. Repo1 is in items_to_process. -> Repo1 added to epfl_related_items.
        
        # UserA is NOT added to epfl_related_items by this logic unless it's connected to another EPFL item.
        # But UserA is a user.
        
        # So order should be:
        # Group 0 (EPFL related):
        #   Repo1 (type=repo, prio=2)
        
        # Group 1 (Others):
        #   UserA (type=user, prio=1)
        #   UserB (type=user, prio=1)
        #   UserC (type=user, prio=1)
        #   Repo2 (type=repo, prio=2)
        #   Repo3 (type=repo, prio=2)
        
        # Wait, type_priority is 2nd key.
        # So Repo1 (Group 0) comes before UserA (Group 1).
        
        if "Repo1" in download_order and "Repo2" in download_order:
            idx1 = download_order.index("Repo1")
            idx2 = download_order.index("Repo2")
            print(f"Repo1 index: {idx1}, Repo2 index: {idx2}")
            
            if idx1 < idx2:
                print("✅ SUCCESS: Repo1 was prioritized over Repo2")
            else:
                print("❌ FAILURE: Repo1 was NOT prioritized over Repo2")
        else:
            print("❌ FAILURE: Items not found in download list")
            
    finally:
        # Restore
        process_metadata.MetadataProcessor.download_all_metadata = original_download
        # Cleanup
        if base_dir.exists():
            shutil.rmtree(base_dir)

if __name__ == "__main__":
    test_epfl_prioritization()
