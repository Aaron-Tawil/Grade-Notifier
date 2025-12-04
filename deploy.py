import os
import subprocess
import sys
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

def run_command(command, shell=True):
    """Runs a shell command and exits if it fails."""
    print(f"Running: {command}")
    try:
        subprocess.check_call(command, shell=shell)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        sys.exit(1)

def main():
    print("--- Starting Deployment ---")

    # Configuration
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        project_id = input("Enter Google Cloud Project ID: ").strip()
        if not project_id:
            print("Project ID is required.")
            sys.exit(1)

    region = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
    repo_name = os.getenv("ARTIFACT_REPO_NAME", "grade-notifier-repo")
    image_name = os.getenv("IMAGE_NAME", "grade-notifier-image")
    service_name = os.getenv("SERVICE_NAME", "grade-notifier-service")
    service_account = f"grade-notifier-sa@{project_id}.iam.gserviceaccount.com"

    full_image_name = f"{region}-docker.pkg.dev/{project_id}/{repo_name}/{image_name}:latest"

    print(f"Project ID: {project_id}")
    print(f"Region: {region}")
    print(f"Image: {full_image_name}")
    print(f"Service: {service_name}")

    # Check for prod.env.yaml
    if not os.path.exists("prod.env.yaml"):
        print("Error: prod.env.yaml not found. Please create it with your production secrets.")
        sys.exit(1)

    # 1. Build Docker Image
    print("\n[1/3] Building Docker Image...")
    run_command(f"docker build -t {full_image_name} .")

    # 2. Push to Artifact Registry
    print("\n[2/3] Pushing to Artifact Registry...")
    run_command(f"docker push {full_image_name}")

    # 3. Deploy to Cloud Run
    print("\n[3/3] Deploying to Cloud Run...")
    deploy_cmd = (
        f"gcloud run deploy {service_name} "
        f"--image={full_image_name} "
        f"--region={region} "
        f"--env-vars-file=prod.env.yaml "
        f"--service-account={service_account} "
        f"--project={project_id} "
        f"--no-allow-unauthenticated"
    )
    run_command(deploy_cmd)

    print("\n--- Deployment Complete! ---")

if __name__ == "__main__":
    main()
