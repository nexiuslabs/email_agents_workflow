# src/your_project/tools/fetch_email_thread_tools.py

import os
import json
import requests
from datetime import datetime
from msal import ConfidentialClientApplication
from crewai.tools import tool
from db_utils import get_mail_id_by_task_id

def get_access_token():
    """Acquire an app-only token from Azure AD via MSAL."""
    client_id     = os.getenv("CLIENT_ID")
    tenant_id     = os.getenv("TENANT_ID")
    client_secret = os.getenv("CLIENT_SECRET")
    authority     = f"https://login.microsoftonline.com/{tenant_id}"

    app = ConfidentialClientApplication(
        client_id=client_id,
        authority=authority,
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" in result:
        return result["access_token"]
    raise RuntimeError(f"Token acquisition failed: {result.get('error_description')}")

def fetch_email_thread(user_email: str, mail_id: str) -> dict:
    """Core function to fetch thread, returns dict."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.body-content-type="text"',
    }

    # Fetch primary message to get conversationId
    url_primary = f"https://graph.microsoft.com/v1.0/users/{user_email}/messages/{mail_id}"
    resp = requests.get(url_primary, headers=headers)
    resp.raise_for_status()
    primary = resp.json()
    conversation_id = primary["conversationId"]

    # Fetch all messages in conversation
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/messages"
    params = {
        "$filter": f"conversationId eq '{conversation_id}'",
        "$select": "id,from,toRecipients,subject,receivedDateTime,body,internetMessageId",
        "$top": "50",
    }
    all_messages = []
    while url:
        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        all_messages.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None  # only use params on first call

    # De-duplicate
    unique = {}
    for m in all_messages:
        key = m.get("internetMessageId") or m["id"]
        if key not in unique:
            unique[key] = m

    # Sort chronologically
    msgs = list(unique.values())
    msgs.sort(key=lambda m: datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")))

    # Build result
    result = {
        "conversationId": conversation_id,
        "messages": [
            {
                "id": m["id"],
                "from": m["from"]["emailAddress"]["address"],
                "to": [r["emailAddress"]["address"] for r in m.get("toRecipients", [])],
                "subject": m.get("subject"),
                "receivedDateTime": m.get("receivedDateTime"),
                "body": m.get("body", {}).get("content", "").strip(),
            }
            for m in msgs
        ],
    }

    return result

@tool("FetchEmailThreadTool")
def fetch_email_thread_tool(user_email: str, mail_id: str) -> str:
    """
    Fetches the full email thread for a given user's mail ID from Microsoft Graph,
    returns the conversation as a JSON string.
    """
    result = fetch_email_thread(user_email, mail_id)
    return json.dumps(result, indent=2)

@tool("FetchEmailThreadByTaskIdTool")
def fetch_email_thread_by_task_id_tool(task_id: str, sender_email: str) -> str:
    """
    Given task_id and sender_email, fetches the full email thread.
    Returns the conversation JSON as a string.
    """
    mail_id = get_mail_id_by_task_id(task_id)
    if not mail_id:
        raise ValueError(f"No mail_id found for task_id {task_id}.")

    result = fetch_email_thread(sender_email, mail_id)
    return json.dumps(result, indent=2)
