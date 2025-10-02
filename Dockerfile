# Use the official Playwright image which has all browsers and dependencies installed.
FROM mcr.microsoft.com/playwright/python:v1.49.0

# Set the working directory
WORKDIR /app

# Copy requirements and install only the non-Playwright packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Set the entrypoint for the function
CMD ["functions-framework", "--target=main", "--port=8080"]