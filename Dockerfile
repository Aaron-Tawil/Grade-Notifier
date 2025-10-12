# Start with a slim Python base image
FROM python:3.11-slim

WORKDIR /app

# Install all OS dependencies needed by Playwright/Chromium
# This includes the libraries that were missing in the previous version.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libxkbcommon0 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxtst6 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install only the Chromium browser.
# The ENV var tells playwright where to install it.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium

# Copy the application code
COPY . .

# Set the command to run the application.
# Functions-framework will automatically use the $PORT env var provided by Cloud Run.
CMD ["functions-framework", "--target=main", "--port=8080"]