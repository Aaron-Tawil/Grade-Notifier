import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent))

from main import (
    monitor_with_ims,
    save_cache_to_gcs,
    IMS_CACHE_FILE_NAME,
)

TEST_DATA_DIR = Path(__file__).parent / "data"
LOCAL_CACHE_PATH = TEST_DATA_DIR / "grades_cache_ims.json"

def seed_cache():
    print("--- Seeding IMS Cache ---")
    if LOCAL_CACHE_PATH.exists():
        try:
            with open(LOCAL_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"Uploading {IMS_CACHE_FILE_NAME} to GCS...")
            save_cache_to_gcs(data, IMS_CACHE_FILE_NAME)
            print("Done.")
        except Exception as e:
            print(f"Error seeding cache: {e}")
    else:
        print(f"Warning: {LOCAL_CACHE_PATH} not found.")

def main():
    seed_cache()
    print("\n--- Running Monitor with IMS ---")
    monitor_with_ims()

if __name__ == "__main__":
    main()
