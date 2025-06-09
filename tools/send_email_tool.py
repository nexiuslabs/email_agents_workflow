import os
import logging
import base64
from msal import ConfidentialClientApplication
from functools import lru_cache
import requests
from pybreaker import CircuitBreaker
from crewai.tools import tool
import urllib.parse

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INLINE_ATTACHMENT_LIMIT = 4 * 1024 * 1024  # 4MB
CHUNK_SIZE = 5 * 1024 * 1024

email_breaker = CircuitBreaker(fail_max=5, reset_timeout=60)

@lru_cache()
def get_graph_app() -> ConfidentialClientApplication:
    return ConfidentialClientApplication(
        client_id=os.getenv("CLIENT_ID"),
        client_credential=os.getenv("CLIENT_SECRET"),
        authority=f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}"
    )

def upload_large_attachment(upload_url: str, buffer: bytes):
    total = len(buffer)
    start = 0
    while start < total:
        end = min(start + CHUNK_SIZE, total) - 1
        chunk = buffer[start:end + 1]
        headers = {
            'Content-Length': str(len(chunk)),
            'Content-Range': f'bytes {start}-{end}/{total}',
        }
        resp = requests.put(upload_url, headers=headers, data=chunk, timeout=30)
        resp.raise_for_status()
        start = end + 1
        logger.info(f"✅ Uploaded chunk {start}/{total}")

@tool("CrewAI: Send email (with optional attachments) via Microsoft Graph API.")
def send_email(sender: str, receiver: str, subject: str, content: str, attachments: list = None) -> bool:
    """
    Sends an email from sender to receiver with optional attachments.
    Attachments must be passed in as:
    [{"filename": ..., "size": ..., "content": ..., "content_type": ...}]
    """
    try:
        logger.info("🚀 Starting send_email tool")

        # ✅ Fix: ensure attachments is always a list
        if attachments is None or not isinstance(attachments, list):
            attachments = []

        # ✅ Optional: validate structure of each attachment
        for f in attachments:
            if not all(k in f for k in ("filename", "content", "size")):
                raise ValueError(f"Invalid attachment format: {f}")

        app = get_graph_app()
        token_resp = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        access_token = token_resp.get("access_token")
        if not access_token:
            logger.error(f"❌ Token error: {token_resp}")
            raise RuntimeError("Failed to acquire Microsoft Graph access token")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        inline_attachments = []
        large_files = []
        for f in attachments:
            if f["size"] <= INLINE_ATTACHMENT_LIMIT:
                inline_attachments.append({
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": f["filename"],
                    "contentType": f.get("content_type", "application/octet-stream"),
                    "contentBytes": f["content"]
                })
            else:
                large_files.append(f)

        # Create the draft message
        draft = {
            "subject": subject,
            "body": {"contentType": "Text", "content": content},
            "toRecipients": [{"emailAddress": {"address": receiver}}]
        }
        if inline_attachments:
            draft["attachments"] = inline_attachments

        draft_url = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(sender)}/messages"
        draft_resp = requests.post(draft_url, json=draft, headers=headers)
        if draft_resp.status_code not in (200, 201):
            logger.error(f"❌ Draft creation failed: {draft_resp.status_code} {draft_resp.text}")
            raise RuntimeError(f"Failed to create draft: {draft_resp.text}")

        draft_id = draft_resp.json().get("id")

        # Upload large attachments
        for f in large_files:
            logger.info(f"📎 Uploading large file: {f['filename']} ({f['size']} bytes)")
            session_url = (
                f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(sender)}"
                f"/messages/{draft_id}/attachments/createUploadSession"
            )
            session_payload = {
                "attachmentItem": {
                    "attachmentType": "file",
                    "name": f["filename"],
                    "size": f["size"],
                    "contentType": f.get("content_type", "application/octet-stream")
                }
            }
            sess_resp = requests.post(session_url, json=session_payload, headers=headers)
            if not sess_resp.ok:
                logger.error(f"❌ Upload session failed: {sess_resp.status_code} {sess_resp.text}")
                raise RuntimeError(f"Upload session creation failed: {sess_resp.text}")

            upload_url = sess_resp.json().get("uploadUrl")
            buffer = base64.b64decode(f["content"])
            upload_large_attachment(upload_url, buffer)

        # Send the email
        send_url = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(sender)}/messages/{draft_id}/send"
        send_resp = email_breaker.call(requests.post, send_url, headers=headers)
        if send_resp.status_code == 202:
            logger.info("✅ Email successfully sent")
            return True
        else:
            logger.error(f"❌ Send failed: {send_resp.status_code} {send_resp.text}")
            raise RuntimeError(f"Failed to send email: {send_resp.text}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise RuntimeError(f"send_email failed: {e}")
