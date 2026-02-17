import os
import sys
import logging
import json
from dotenv import load_dotenv
from robust_scraper import RobustGradesScraper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("VerifyModule")
logging.getLogger("robust_scraper").setLevel(logging.DEBUG)

load_dotenv()

def run_test():
    username = os.getenv("UNI_USER")
    password = os.getenv("UNI_PASS")
    id_num = os.getenv("UNI_ID")
    
    if not all([username, password, id_num]):
        logger.error("Missing credentials.")
        return

    logger.info("Testing RobustGradesScraper module...")
    with RobustGradesScraper(headless=True) as scraper:
        if scraper.login(username, password, id_num):
            exams = scraper.scrape()
            logger.info(f"Scraped {len(exams)} exams.")
            
            # Save to JSON for inspection
            with open("scraped_exams.json", "w", encoding="utf-8") as f:
                json.dump(exams, f, ensure_ascii=False, indent=4)
            logger.info("Saved extracted exams to 'scraped_exams.json'")
            
            # Log notebook button details for ALL exams to find the difference
            for i, exam in enumerate(exams):
                # We need to re-find the original row to get the element handle if we want to log HTML
                # But here we only have the dict. 
                # Let's trust the debug output we will add to robust_scraper.py instead.
                pass

            if len(exams) > 0:
                logger.info(f"TEST PASSED: {len(exams)} exams extracted.")
            else:
                logger.error("TEST FAILED: No exams found.")
        else:
            logger.error("TEST FAILED: Login failed.")

if __name__ == "__main__":
    run_test()
