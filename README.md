# TAU Grade Notifier

![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

A serverless, automated script that checks for grade updates on the Tel Aviv University student portal and sends a detailed notification to your phone via Telegram when a change is detected.

## Features

-   **Automated Login:** Securely logs into the TAU student portal using headless browser automation.
-   **Intelligent Change Detection:** Notifies you for both new grades and changes to exam notebook availability.
-   **Persistent Caching:** Uses Google Cloud Storage (GCS) to store a cache of your grades, ensuring it only notifies you about actual changes.
-   **Detailed Notifications:** Sends specific, formatted messages via Telegram detailing exactly what changed (e.g., "Grade changed from `95` to `100`" or "Notebook is now available").
-   **Serverless Deployment:** Designed to be deployed on Google Cloud as a cost-effective and maintenance-free Cloud Function.

## How It Works

The system runs on a fully automated, serverless architecture:

1.  **Cloud Scheduler:** A cron job triggers the system every 10 minutes.
2.  **Cloud Function:** The scheduler invokes a secure Cloud Function.
3.  **Scraping:** The function launches a headless Playwright instance, logs into the TAU portal, and scrapes the grades table.
4.  **Comparison:** The scraped data is compared against the previous version stored in a `.json` file in a Google Cloud Storage bucket.
5.  **Notification & Caching:** If a difference is found, the script sends a detailed notification to your Telegram and updates the cache file in GCS with the new data for the next run.

## Local Setup and Usage

### Prerequisites

-   Python 3.11+
-   Git
-   [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (for deployment)

### Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd grade_notyfier
    ```

2.  **Install Python dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Install Playwright browsers:**
    This is a crucial step to download the browser executables that Playwright needs.
    ```bash
    playwright install
    ```

### Configuration

Create a `.env` file in the root of the project for local development. You can use the provided variables as a template:

```env
# --- Credentials ---
UNI_USER=your_tau_username
UNI_PASS=your_tau_password
UNI_ID=your_tau_id_number

# --- Telegram Bot ---
# Create a bot with @BotFather on Telegram to get the token
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
# Get your chat ID from @userinfobot on Telegram
TELEGRAM_CHAT_ID=your_telegram_chat_id

# --- GCS (Optional for local runs, but required for saving state) ---
GCS_BUCKET_NAME=your-gcs-bucket-name
GOOGLE_APPLICATION_CREDENTIALS=path/to/your/gcs_credentials.json

# --- Development ---
# Set to 1 to run the browser in non-headless mode for debugging
RUN_HEADFUL=0
```

### Running Locally

Execute the script from the command line:
```bash
python main.py
```

## Deployment

### Part A: General Deployment Instructions (Theoretical Host)

You can run this script on any host that has Python and can run scheduled jobs (e.g., a VPS, Raspberry Pi, Heroku).

1.  **Environment Variables:** Set the required environment variables (`UNI_USER`, `UNI_PASS`, `TELEGRAM_BOT_TOKEN`, etc.) on your host system.
2.  **Install Dependencies:** Ensure all Python packages from `requirements.txt` and the Playwright browsers are installed on the machine.
3.  **Scheduler:** Use a system scheduler like `cron` to run the script automatically. For example, to run it every 10 minutes, your crontab entry would look like this:
    ```cron
    */10 * * * * /usr/bin/python3 /path/to/your/project/main.py >> /path/to/your/project/cron.log 2>&1
    ```

### Part B: Google Cloud Deployment (Specific Instructions)

This project is designed for a serverless deployment on Google Cloud, which is highly cost-effective and requires no server management.

1.  **Initial Setup:**
    -   Authenticate with the gcloud CLI: `gcloud auth login`
    -   Set your project: `gcloud config set project YOUR_PROJECT_ID`
    -   Enable required APIs:
        ```bash
        gcloud services enable cloudfunctions.googleapis.com cloudscheduler.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com run.googleapis.com iam.googleapis.com
        ```

2.  **Create GCS Bucket:**
    Create a bucket to store the grades cache.
    ```bash
    gcloud storage buckets create gs://your-unique-bucket-name --location=us-central1
    ```

3.  **Create Service Account:**
    Create a dedicated identity for the script to use.
    ```bash
    gcloud iam service-accounts create grade-notifier-sa --display-name="Grade Notifier Service Account"
    ```
    Grant it permission to access the bucket:
    ```bash
    gcloud storage buckets add-iam-policy-binding gs://your-unique-bucket-name --member="serviceAccount:grade-notifier-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" --role="roles/storage.objectAdmin"
    ```

4.  **Create Production Environment File:**
    Create a file named `prod.env.yaml` in your project root with your production secrets:
    ```yaml
    UNI_USER: \'your_tau_username\'
    UNI_PASS: \'your_tau_password\'
    # ... add all other required variables
    ```

5.  **Deploy the Cloud Function:**
    This command packages and deploys your function.
    ```bash
    gcloud functions deploy grade-notifier-function \
      --gen2 \
      --region=us-central1 \
      --runtime=python311 \
      --source=. \
      --entry-point=main \
      --trigger-http \
      --no-allow-unauthenticated \
      --run-service-account=grade-notifier-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
      --timeout=300s \
      --env-vars-file=prod.env.yaml
    ```
    *Note: The build process will automatically run the `gcp-build` script in `package.json` to install Playwright's browsers.*

6.  **Create the Cloud Scheduler Job:**
    First, get your function's trigger URL from the deployment output. Then, create the scheduler job to trigger it every 10 minutes.
    ```bash
    gcloud scheduler jobs create http grade-notifier-job \
      --schedule="*/10 * * * *" \
      --uri="YOUR_FUNCTION_TRIGGER_URL" \
      --http-method=POST \
      --oidc-service-account-email=grade-notifier-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
      --location=us-central1
    ```

## Configuration Variables

| Variable                         | Description                                                                 | Required for Local | Required for Cloud |
| -------------------------------- | --------------------------------------------------------------------------- | ------------------ | ------------------ |
| `UNI_USER`                       | Your username for the TAU portal.                                           | **Yes**            | **Yes**            |
| `UNI_PASS`                       | Your password for the TAU portal.                                           | **Yes**            | **Yes**            |
| `UNI_ID`                         | Your 9-digit national ID number for the TAU portal.                         | **Yes**            | **Yes**            |
| `TELEGRAM_BOT_TOKEN`             | The secret token for your Telegram bot.                                     | **Yes**            | **Yes**            |
| `TELEGRAM_CHAT_ID`               | The chat ID to which the bot will send messages.                            | **Yes**            | **Yes**            |
| `GCS_BUCKET_NAME`                | The name of the Google Cloud Storage bucket for caching.                    | No                 | **Yes**            |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the GCS credentials JSON file (for local auth).                     | No                 | No                 |
| `RUN_HEADFUL`                    | Set to `1` to run the browser with a visible UI for debugging.              | No                 | No                 |

## License

This project is distributed under the MIT License. See the `LICENSE` file for more information.
