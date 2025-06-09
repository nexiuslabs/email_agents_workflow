import os
import logging
from msal import ConfidentialClientApplication
import requests
from crewai.tools import tool
from db_utils import get_mail_id_by_task_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_graph_app():
    return ConfidentialClientApplication(
        client_id=os.getenv("CLIENT_ID"),
        client_credential=os.getenv("CLIENT_SECRET"),
        authority=f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}"
    )

@tool("CrewAI: Read email body using Graph API based on Task ID")
def read_email_by_task_id(task_id: int, sender_email: str) -> str:
    """
    Given a CrewAI Task ID and sender_email, this tool reads the full content of the corresponding email
    using Microsoft Graph API.
    """
    logger.info(f"📨 Task ID: {task_id}")

    try:
        logger.info(f"🔎 Reading email for task ID: {task_id}")

        mail_id = get_mail_id_by_task_id(task_id)
        logger.info(f"📨 Mail ID: {mail_id}")
        if not mail_id:
            raise ValueError(f"No email ID found for task {task_id}")

        if not sender_email:
            raise EnvironmentError("sender_email must be provided")

        app = get_graph_app()
        token_resp = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        access_token = token_resp.get("access_token")
        if not access_token:
            raise RuntimeError("Failed to acquire Graph access token")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/messages/{mail_id}"
        logger.info(f"📨 URL: {url}")
        response = requests.get(url, headers=headers)
        if not response.ok:
            raise RuntimeError(f"Failed to fetch email: {response.status_code} {response.text}")

        email_data = response.json()
        subject = email_data.get("subject", "(No Subject)")
        body = email_data.get("body", {}).get("content", "")

        return f"Subject: {subject}\n\n{body}"

    except Exception as e:
        logger.exception("❌ Error reading email")
        raise RuntimeError(f"read_email_by_task_id failed: {e}")
