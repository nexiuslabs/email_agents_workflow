# src/your_project/tools/get_last_non_user_message_tool.py
import os
import json
import requests
from datetime import datetime
from msal import ConfidentialClientApplication
from crewai.tools import tool

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

def get_last_non_user_message(user_email: str, mail_id: str) -> dict:
    """Returns the most recent message in thread not sent by user."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.body-content-type="text"',
    }

    # Step 1: Fetch base message to get conversationId
    url_primary = f"https://graph.microsoft.com/v1.0/users/{user_email}/messages/{mail_id}"
    resp = requests.get(url_primary, headers=headers)
    resp.raise_for_status()
    primary = resp.json()
    conversation_id = primary["conversationId"]

    # Step 2: Fetch all messages in that conversation
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
        params = None  # use params only on first call

    # Step 3: Deduplicate by message ID
    unique = {}
    for m in all_messages:
        key = m.get("internetMessageId") or m["id"]
        if key not in unique:
            unique[key] = m

    # Step 4: Sort chronologically
    msgs = list(unique.values())
    msgs.sort(key=lambda m: datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")))

    # Step 5: Find last message NOT from current user
    non_user_msgs = [
        m for m in msgs if m["from"]["emailAddress"]["address"].lower() != user_email.lower()
    ]
    if not non_user_msgs:
        return {"message": "No non-user messages found in this thread."}

    last_msg = non_user_msgs[-1]
    return {
        "id": last_msg["id"],
        "from": last_msg["from"]["emailAddress"]["address"],
        "to": [r["emailAddress"]["address"] for r in last_msg.get("toRecipients", [])],
        "subject": last_msg.get("subject"),
        "receivedDateTime": last_msg.get("receivedDateTime"),
        "body": last_msg.get("body", {}).get("content", "").strip(),
    }

@tool("GetLastRecipientMessageTool")
def get_last_recipient_message_tool(user_email: str, mail_id: str) -> str:
    """
    Fetches the most recent message in an email thread that was NOT sent by the current user.
    Inputs:
    - user_email: The user whose mailbox to query
    - mail_id: The ID of the email to find the thread from
    Returns the message in JSON format.
    """
    result = get_last_non_user_message(user_email, mail_id)
    return json.dumps(result, indent=2)
