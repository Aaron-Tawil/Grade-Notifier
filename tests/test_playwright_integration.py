import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent))

from main import (
    monitor_with_playwright,
    save_cache_to_gcs,
    CACHE_FILE_NAME,
)

TEST_DATA_DIR = Path(__file__).parent / "data"
LOCAL_CACHE_PATH = TEST_DATA_DIR / "grades_cache.json"

def seed_cache():
    print("--- Seeding Playwright Cache ---")
    if LOCAL_CACHE_PATH.exists():
        try:
            with open(LOCAL_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"Uploading {CACHE_FILE_NAME} to GCS...")
            save_cache_to_gcs(data, CACHE_FILE_NAME)
            print("Done.")
        except Exception as e:
            print(f"Error seeding cache: {e}")
    else:
        print(f"Warning: {LOCAL_CACHE_PATH} not found.")

def main():
    seed_cache()
    print("\n--- Running Monitor with Playwright ---")
    monitor_with_playwright()

if __name__ == "__main__":
    main()
