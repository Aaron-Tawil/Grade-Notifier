# TAU Grade Notifier

![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

> [!WARNING]
> This project relies on web scraping and is not an official API. The target website's structure may change at any time, which would break the script. This project is provided as-is and may no longer be actively maintained.

A serverless, automated script that checks for grade updates on TAU University student portal and sends a detailed notification to your phone via Telegram when a change is detected.

## Features

-   **Dual Monitoring Strategy:** The script uses two independent methods to ensure reliability. It scrapes the main student portal and also queries the underlying IMS system directly.
-   **Automated Login:** Securely logs into the TAU student portal using headless browser automation.
-   **Intelligent Change Detection:** Notifies you for both new grades and changes to exam notebook availability.
-   **Persistent Caching:** Uses Google Cloud Storage (GCS) to store a cache of your grades, ensuring it only notifies you about actual changes.
-   **Detailed Notifications:** Sends specific, formatted messages via Telegram detailing exactly what changed (e.g., "Grade changed from `95` to `100`" or "Notebook is now available").
-   **Serverless Deployment:** Designed to be deployed on Google Cloud as a cost-effective and maintenance-free Cloud Run service.

## How It Works

The system runs on a fully automated, serverless architecture using Docker on Google Cloud. It employs a dual-method approach for maximum reliability:

1.  **Cloud Scheduler:** A cron job triggers the system on your defined schedule (e.g., every 10 minutes from Sunday to Thursday).
2.  **Cloud Run:** The scheduler invokes a secure Cloud Run service, which runs the Docker container.
3.  **Monitoring & Scraping:**
    -   **IMS API (Primary Method):** The script first makes direct requests to the university's backend IMS system. This method is fast, efficient, and less prone to breaking as it does not rely on the website's visual layout. It fetches grade data in a structured format.
    -   **Playwright Web Scraping (Secondary Method):** The application also launches a headless Playwright instance, logs into the main TAU portal, and scrapes the visual grades table. This acts as a fallback and captures details that may not be available via the IMS system, such as exam notebook availability.
4.  **Comparison:** The data from both sources is canonicalized and compared against the previous version stored in a `.json` file in a Google Cloud Storage bucket.
5.  **Notification & Caching:** If a difference is found, the script sends a detailed notification to your Telegram and updates the cache file in GCS with the new data for the next run.

## Local Setup and Usage

### Prerequisites

-   Python 3.11+
-   Git
-   Docker (for deployment)
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

### Part B: Google Cloud Deployment (Cloud Run with Docker)

This project is designed for a serverless deployment on Google Cloud Run. We provide a helper script `deploy.py` to automate the build and deploy process.

1.  **Initial Setup:**
    -   Authenticate with the gcloud CLI: `gcloud auth login`
    -   Set your project: `gcloud config set project YOUR_PROJECT_ID`
    -   Enable required APIs:
        ```bash
        gcloud services enable run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com iam.googleapis.com storage.googleapis.com
        ```

2.  **Create Resources (One-time setup):**
    -   **GCS Bucket:** Create a bucket for the cache (e.g., `gs://your-unique-bucket-name`).
    -   **Service Account:** Create a service account and grant it `roles/storage.objectAdmin` on the bucket.
    -   **Artifact Registry:** Ensure you have authenticated Docker with Google Cloud:
        ```bash
        gcloud auth configure-docker us-central1-docker.pkg.dev
        ```

3.  **Configuration:**
    -   Create `prod.env.yaml` with your production secrets (see "Configuration" section).
    -   Ensure your `.env` file has `GOOGLE_CLOUD_PROJECT` set, or be ready to enter it when prompted.

4.  **Deploy:**
    Run the deployment script:
    ```bash
    python deploy.py
    ```
    This script will:
    -   Build the Docker image.
    -   Push it to Google Artifact Registry.
    -   Deploy the service to Cloud Run.

5.  **Create Cloud Scheduler Job:**
    (This is a one-time setup)
    ```bash
    gcloud scheduler jobs create http grade-notifier-job \
      --schedule="*/10 8-23 * * 0-4" \
      --time-zone="Asia/Jerusalem" \
      --uri="YOUR_CLOUD_RUN_SERVICE_URL" \
      --http-method=POST \
      --oidc-service-account-email=grade-notifier-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
      --location=us-central1
    ```
    *(Replace `YOUR_CLOUD_RUN_SERVICE_URL` with the URL provided in the output of the `deploy.py` script).*

## Development Workflow

To make changes to the application (e.g., add a new feature or fix a bug):

1.  **Local Development:**
    -   Modify the Python code in `main.py`.
    -   Test your changes thoroughly by running `python main.py` locally.

2.  **Deploy Changes:**
    -   Run `python deploy.py`.
    -   This automatically builds a new Docker image, pushes it to the registry, and deploys the new revision to Cloud Run.

## License

This project is distributed under the MIT License. See the `LICENSE` file for more information.