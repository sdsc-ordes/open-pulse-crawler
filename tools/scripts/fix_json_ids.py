import json
import os
import argparse
from urllib.parse import urlparse

def fix_json_ids(folder_path):
    if not os.path.exists(folder_path):
        print(f"Error: Folder '{folder_path}' does not exist.")
        return

    print(f"Scanning folder: {folder_path}")
    
    files_processed = 0
    files_updated = 0

    for filename in os.listdir(folder_path):
        if not filename.endswith('.json'):
            continue
            
        file_path = os.path.join(folder_path, filename)
        files_processed += 1
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Check if we need to update
            needs_update = False
            
            # Ensure structure exists
            if 'link' in data and 'output' in data:
                current_id = data['output'].get('id', '')
                
                # Check if id is empty or missing
                if not current_id:
                    link = data['link']
                    
                    # User requested to use the full github link as the ID
                    new_id = link
                    
                    if new_id:
                        print(f"Updating {filename}: ID '' -> '{new_id}'")
                        data['output']['id'] = new_id
                        needs_update = True
            
            if needs_update:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                files_updated += 1
                
        except json.JSONDecodeError:
            print(f"Error decoding JSON in file: {filename}")
        except Exception as e:
            print(f"Error processing file {filename}: {e}")

    print(f"\nSummary:")
    print(f"Processed {files_processed} files.")
    print(f"Updated {files_updated} files.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix missing IDs in JSON files based on GitHub links.")
    parser.add_argument("folder", help="Path to the folder containing JSON files")
    
    args = parser.parse_args()
    
    fix_json_ids(args.folder)
