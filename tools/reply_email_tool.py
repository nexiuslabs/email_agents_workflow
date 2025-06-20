import os
import logging
import base64
import requests
from msal import ConfidentialClientApplication
from pathlib import Path
from crewai.tools import tool

INLINE_ATTACHMENT_LIMIT = 4 * 1024 * 1024
CHUNK_SIZE = 5 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_graph_token():
    app = ConfidentialClientApplication(
        client_id=os.getenv("CLIENT_ID"),
        client_credential=os.getenv("CLIENT_SECRET"),
        authority=f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}"
    )
    token = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if not token.get("access_token"):
        raise RuntimeError("Failed to acquire token")
    return token["access_token"]


def get_latest_message_id(graph_token, sender_email):
    url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/mailFolders/inbox/messages?$orderby=receivedDateTime desc&$top=50&$select=id,from"
    headers = {"Authorization": f"Bearer {graph_token}"}
    res = requests.get(url, headers=headers)
    if not res.ok:
        raise RuntimeError(f"Failed to fetch messages: {res.status_code} {res.text}")
    for msg in res.json().get("value", []):
        msg_sender = msg["from"]["emailAddress"]["address"]
        if msg_sender.lower() != sender_email.lower():
            return msg["id"]
    raise RuntimeError("No incoming message to reply to")


def upload_large_attachment(graph_token, upload_url, buffer):
    total = len(buffer)
    start = 0
    while start < total:
        end = min(start + CHUNK_SIZE, total) - 1
        chunk = buffer[start:end+1]
        headers = {
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{end}/{total}"
        }
        res = requests.put(upload_url, headers=headers, data=chunk)
        if res.status_code not in (200, 201, 202):
            raise RuntimeError(f"Chunk upload failed: {res.status_code} {res.text}")
        start = end + 1


@tool("CrewAI: Reply to an email by ID")
def reply_to_latest_email(sender_email: str, comment: str, attachment: str, mail_id: str) -> str:
    """
    Replies to a specific email by ID.
    Supports inline attachments (<4MB) and large file uploads (>4MB).
    attachment is a comma-separated list of full file paths.
    mail_id is the ID of the message to reply to.
    """
    try:
        logger.info("✉️ Starting reply process...")
        token = get_graph_token()
        if not mail_id:
            raise ValueError("mail_id is required")
        message_id = mail_id
        logger.info(f"📨 Replying to message ID: {message_id}")

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        paths = [p.strip() for p in (attachment or "").split(",") if p.strip()]
        inline_attachments, large_files = [], []

        for file_path in paths:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"Attachment not found: {file_path}")
            data = path.read_bytes()
            if len(data) <= INLINE_ATTACHMENT_LIMIT:
                inline_attachments.append({
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": path.name,
                    "contentType": "application/octet-stream",
                    "contentBytes": base64.b64encode(data).decode()
                })
            else:
                large_files.append((path, data))

        if not inline_attachments and not large_files:
            url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/messages/{message_id}/reply"
            response = requests.post(url, headers=headers, json={"comment": comment})
            if not response.ok:
                raise RuntimeError(f"Reply failed: {response.status_code} {response.text}")
            return "✅ Reply sent without attachments."

        # create draft with inline attachments
        draft_url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/messages/{message_id}/createReply"
        draft_payload = {
            "message": {
                "body": {"contentType": "Text", "content": comment},
                "attachments": inline_attachments
            }
        }
        draft_res = requests.post(draft_url, headers=headers, json=draft_payload)
        if not draft_res.ok:
            raise RuntimeError(f"Draft creation failed: {draft_res.status_code} {draft_res.text}")
        draft_id = draft_res.json()["id"]
        logger.info(f"📝 Draft created: {draft_id}")

        # upload large files
        for path, data in large_files:
            session_url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/messages/{draft_id}/attachments/createUploadSession"
            session_payload = {
                "attachmentItem": {
                    "attachmentType": "file",
                    "name": path.name,
                    "size": len(data),
                    "contentType": "application/octet-stream"
                }
            }
            session_res = requests.post(session_url, headers=headers, json=session_payload)
            if not session_res.ok:
                raise RuntimeError(f"Upload session failed: {session_res.status_code} {session_res.text}")
            upload_url = session_res.json()["uploadUrl"]
            upload_large_attachment(token, upload_url, data)
            logger.info(f"📤 Uploaded large file: {path.name}")

        send_url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/messages/{draft_id}/send"
        send_res = requests.post(send_url, headers=headers)
        if not send_res.ok:
            raise RuntimeError(f"Send failed: {send_res.status_code} {send_res.text}")

        return "✅ Reply sent with attachments."
    except Exception as e:
        logger.exception("❌ Error replying to email")
        raise RuntimeError(f"reply_to_latest_email failed: {e}")
