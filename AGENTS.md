# Project: Grade Notifier                                                                                                        
                                                                                                                                   
  This document outlines the plan and requirements for developing the Grade Notifier application, a web scraping and notification  
  system.                                                                                                                          
                                                                                                                                   
  ## Project Goal                                                                                                                  
                                                                                                                                   
  The primary goal of this project is to create a fully automated script that monitors a university's grades page for changes.     
  When a new grade is posted or an existing one is updated, the script should send a notification and log the change. The entire   
  solution will be deployed on Google Cloud Platform (GCP) to ensure reliability and scalability.                                  
                                                                                                                                   
  ## Proposed Architecture                                                                                                         
                                                                                                                                   
  The application will be built using a serverless architecture on GCP:                                                            
                                                                                                                                   
  - **Cloud Scheduler:** Triggers the scraping process every 5 minutes.                                                            
  - **Pub/Sub:** A messaging queue that decouples the scheduler from the scraping logic.                                           
  - **Cloud Function:** A Python-based function that executes the web scraping logic using `playwright`.                           
  - **Secret Manager:** Securely stores the website login credentials.                                                             
  - **Cloud Storage:** Persists the scraped data (in a JSON file) and stores debugging artifacts like screenshots.                 
  - **Notification Service:** Integrates with a service like SendGrid (for email) or a Telegram Bot to send notifications.         
  - **Cloud Logging:** Aggregates logs for monitoring and debugging.                                                               
                                                                                                                                   
  ## Requirements for AI Agents                                                                                                    
                                                                                                                                   
  AI agents assisting with this project should be capable of the following tasks:                                                  
                                                                                                                                   
  1.  **Python Development:**                                                                                                      
      - Modify and enhance the existing `tau_grades.py` script.                                                                    
      - Integrate the script with GCP services using the appropriate Python client libraries.                                      
      - Implement robust web scraping logic with `playwright`, including handling of dynamic pages, authentication, and error      
  conditions.                                                                                                                      
      - Write clean, maintainable, and well-documented Python code.                                                                
                                                                                                                                   
  2.  **Google Cloud Platform:**                                                                                                   
      - Generate `gcloud` commands for creating and managing the required GCP resources (Cloud Scheduler, Pub/Sub, Cloud Functions,
  Secret Manager, Cloud Storage).                                                                                                  
      - Configure the necessary permissions (IAM roles) for the Cloud Function to access other GCP services.                       
      - Assist with deploying the Python script to a Cloud Function.                                                               
                                                                                                                                   
  3.  **Web Scraping and Automation:**                                                                                             
      - Debug and troubleshoot `playwright` scripts, particularly issues related to login, navigation, and element selection.      
      - Analyze website structures (HTML, CSS, JavaScript) to identify reliable selectors for scraping.                            
      - Advise on best practices for web scraping to avoid detection and handle anti-bot measures.                                 
                                                                                                                                   
  4.  **General:**                                                                                                                 
      - Understand the overall project architecture and contribute to its implementation.                                          
      - Provide clear explanations of the work being done.                                                                         
      - Follow the instructions and guidelines outlined in this document.                                                          
                                                                                                                                   
  ## Development Data Refresh                                                                                                      
                                                                                                                                   
  During local development or staging, make sure the persisted grade dataset is mutated on each run so test notifications remain   
  meaningful. Delete or rewrite a few sample entries before scraping so the pipeline re-fetches and stores fresh records. Keep this
  behaviour out of production deployments where real grade updates are infrequent.