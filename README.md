# TAU Grade Notifier

![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

A serverless, automated script that checks for grade updates on TAU University student portal and sends a detailed notification to your phone via Telegram when a change is detected.

## Features

-   **Automated Login:** Securely logs into the TAU student portal using headless browser automation.
-   **Intelligent Change Detection:** Notifies you for both new grades and changes to exam notebook availability.
-   **Persistent Caching:** Uses Google Cloud Storage (GCS) to store a cache of your grades, ensuring it only notifies you about actual changes.
-   **Detailed Notifications:** Sends specific, formatted messages via Telegram detailing exactly what changed (e.g., "Grade changed from `95` to `100`" or "Notebook is now available").
-   **Serverless Deployment:** Designed to be deployed on Google Cloud as a cost-effective and maintenance-free Cloud Run service.

## How It Works

The system runs on a fully automated, serverless architecture using Docker on Google Cloud:

1.  **Cloud Scheduler:** A cron job triggers the system on your defined schedule (e.g., every 10 minutes from Sunday to Thursday).
2.  **Cloud Run:** The scheduler invokes a secure Cloud Run service. This service runs a Docker container built from this repository.
3.  **Scraping:** The application launches a headless Playwright instance inside the container, logs into the TAU portal, and scrapes the grades table.
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

### Part B: Google Cloud Deployment (Cloud Run with Docker)

This project is designed for a serverless deployment on Google Cloud Run, which is highly cost-effective and requires no server management. This method involves building and deploying a Docker container.

1.  **Initial Setup:**
    -   Authenticate with the gcloud CLI: `gcloud auth login`
    -   Set your project: `gcloud config set project YOUR_PROJECT_ID`
    -   Enable required APIs for the services we will use:
        ```bash
        gcloud services enable run.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com iam.googleapis.com storage.googleapis.com
        ```

2.  **Create GCS Bucket:**
    Create a bucket to store the grades cache. Choose a globally unique name.
    ```bash
    gcloud storage buckets create gs://your-unique-bucket-name --location=us-central1
    ```

3.  **Create Service Account:**
    Create a dedicated identity for the script to run with.
    ```bash
    gcloud iam service-accounts create grade-notifier-sa --display-name="Grade Notifier Service Account"
    ```
    Grant it permission to read from and write to the bucket:
    ```bash
    gcloud storage buckets add-iam-policy-binding gs://your-unique-bucket-name --member="serviceAccount:grade-notifier-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" --role="roles/storage.objectAdmin"
    ```

4.  **Create Production Environment File:**
    Create a file named `prod.env.yaml` in your project root with your production secrets. This file is already in `.gitignore` and will not be committed.
    ```yaml
    UNI_USER: 'your_tau_username'
    UNI_PASS: 'your_tau_password'
    UNI_ID: 'your_tau_id_number'
    TELEGRAM_BOT_TOKEN: 'your_telegram_bot_token'
    TELEGRAM_CHAT_ID: 'your_telegram_chat_id'
    GCS_BUCKET_NAME: 'your-unique-bucket-name'
    ```

5.  **Build, Push, and Deploy the Service:**
    These steps build your application into a Docker container, push it to Google's Artifact Registry, and deploy it to Cloud Run.

    First, configure Docker to authenticate with Artifact Registry (you only need to do this once):
    ```bash
    gcloud auth configure-docker us-central1-docker.pkg.dev
    ```

    Next, define your full image name. **Replace `YOUR_PROJECT_ID`** with your actual project ID.
    ```bash
    export IMAGE_NAME="us-central1-docker.pkg.dev/YOUR_PROJECT_ID/grade-notifier-repo/grade-notifier-image:latest"
    ```

    Now, build the image using the `Dockerfile`:
    ```bash
    docker build -t $IMAGE_NAME .
    ```

    Push the image to Artifact Registry:
    ```bash
    docker push $IMAGE_NAME
    ```

    Finally, deploy the image to Cloud Run. This command creates a secure service that cannot be accessed publicly.
    ```bash
    gcloud run deploy grade-notifier-service \
      --image=$IMAGE_NAME \
      --region=us-central1 \
      --env-vars-file=prod.env.yaml \
      --service-account=grade-notifier-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
      --no-allow-unauthenticated
    ```

6.  **Create the Cloud Scheduler Job:**
    This job will securely trigger your private Cloud Run service on a schedule.
    ```bash
    gcloud scheduler jobs create http grade-notifier-job \
      --schedule="*/10 8-23 * * 0-4" \
      --time-zone="Asia/Jerusalem" \
      --uri="YOUR_CLOUD_RUN_SERVICE_URL" \
      --http-method=POST \
      --oidc-service-account-email=grade-notifier-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
      --location=us-central1
    ```
    *(Replace `YOUR_CLOUD_RUN_SERVICE_URL` with the URL provided in the output of the `gcloud run deploy` command).*

## License

This project is distributed under the MIT License. See the `LICENSE` file for more information.