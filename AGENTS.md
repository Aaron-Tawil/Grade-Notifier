# Project: Grade Notifier

This document outlines the architecture and technical requirements for the Grade Notifier application. It is intended to be a guide for developers and AI agents working on the project.

## Project Goal

The primary goal of this project is to create a fully automated script that monitors a university's grades page for changes. When a new grade is posted or an existing one is updated, the script should send a notification via Telegram. The entire solution is deployed on Google Cloud Platform (GCP) as a serverless, containerized application.

## Project Architecture

The application is built using a serverless, container-based architecture on GCP:

- **Cloud Scheduler:** A cron job that triggers the service by sending a secure HTTP request on a defined schedule.
- **Cloud Run:** The core service that runs the application logic inside a Docker container. It is configured to be a private service, only invokable by the authenticated scheduler job.
- **Artifact Registry:** A private Docker registry that stores the container images for the application.
- **Cloud Storage:** A storage bucket used to persist the grades cache (as `grades_cache.json`), enabling stateful comparison between runs.
- **Telegram:** The notification service. A bot sends formatted messages to a specified chat ID when changes are detected.
- **Cloud Logging:** Aggregates all logs from the Cloud Run service for monitoring and debugging.

## Development Workflow

To make changes to the application (e.g., add a new feature or fix a bug), an agent should follow this lifecycle:

1.  **Local Development:**
    -   Modify the Python code in `main.py`.
    -   If new libraries are added, update `requirements.txt`.
    -   If new secrets are needed, they should be added to `prod.env.yaml` by the user. The agent should not handle secrets directly.
    -   Local testing can be done via `python main.py`, assuming a correctly configured `.env` file.

2.  **Build & Push the Docker Image:**
    -   The agent should be able to generate the `docker build` and `docker push` commands to create a new image with the code changes.

3.  **Deploy the New Image to Cloud Run:**
    -   The agent should generate the `gcloud run deploy` command to update the service with the new image.

## Requirements for AI Agents

AI agents assisting with this project should be capable of the following tasks:

1.  **Python Development:**
    -   Modify and enhance the main application script, `main.py`.
    -   Integrate the script with GCP services using the appropriate Python client libraries.
    -   Implement robust web scraping logic with `playwright`, including handling of dynamic pages, authentication, and error conditions.
    -   Write clean, maintainable, and well-documented Python code.

2.  **Docker:**
    -   Understand, modify, and optimize the project's `Dockerfile`.
    -   Assist with building and tagging Docker images.
    -   Debug issues related to the container build process or runtime environment.

3.  **Google Cloud Platform:**
    -   Generate `gcloud` commands for managing the required GCP resources: **Cloud Run**, **Cloud Scheduler**, **Artifact Registry**, and **Cloud Storage**.
    -   Configure the necessary permissions (IAM roles) for the Cloud Run service and Cloud Scheduler job.
    -   Assist with pushing Docker images to Artifact Registry.
    -   Assist with deploying new images and configurations to the Cloud Run service.

4.  **Web Scraping and Automation:**
    -   Debug and troubleshoot `playwright` scripts, particularly issues related to login, navigation, and element selection.
    -   Analyze website structures (HTML, CSS, JavaScript) to identify reliable selectors for scraping.
    -   Advise on best practices for web scraping to avoid detection and handle anti-bot measures.

5.  **General:**
    -   Understand the overall project architecture and contribute to its implementation.
    -   Provide clear explanations of the work being done.
    -   Follow the instructions and guidelines outlined in this document.
