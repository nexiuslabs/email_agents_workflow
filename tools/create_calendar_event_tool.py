import os
import msal
import requests
from datetime import timedelta
from dateutil import parser
from dotenv import load_dotenv
from crewai.tools import tool
from tzlocal import get_localzone_name

load_dotenv()

GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"

def get_app_token():
    app = msal.ConfidentialClientApplication(
        client_id=os.getenv("CLIENT_ID"),
        client_credential=os.getenv("CLIENT_SECRET"),
        authority=f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}"
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise Exception("Could not acquire access token")
    return result["access_token"]

def graph_get(endpoint, token, params=None):
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(f"{GRAPH_API_ENDPOINT}{endpoint}", headers=headers, params=params)
    response.raise_for_status()
    return response.json()

def graph_post(endpoint, token, payload):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    response = requests.post(f"{GRAPH_API_ENDPOINT}{endpoint}", headers=headers, json=payload)
    response.raise_for_status()
    return response.json()

def find_upn_by_email(token, email):
    safe_email = email.replace("'", "''")
    params = {
        "$filter": f"mail eq '{safe_email}'",
        "$select": "userPrincipalName"
    }
    data = graph_get("/users", token, params=params)
    if not data.get("value"):
        raise Exception(f"No user found with mail={email}")
    return data["value"][0]["userPrincipalName"]

@tool("CreateCalendarEventTool")
def create_calendar_event_tool(
    sender_email: str,
    subject: str,
    body: str,
    start_datetime: str,
    end_datetime: str,
    time_zone: str,
    location: str,
    attendees: str
) -> str:
    """Create an event in Microsoft 365 calendar and return a confirmation.
    If no end_datetime is provided, it defaults to 1 hour after start_datetime.
    If time_zone is missing or UTC, it uses your local system time zone instead."""

    try:
        # Enforce local timezone if not provided or if UTC
        if not time_zone or time_zone.strip().upper() == "UTC":
            time_zone = get_localzone_name()

        # Parse start and end times
        start_dt = parser.isoparse(start_datetime)
        end_dt = parser.isoparse(end_datetime) if end_datetime else start_dt + timedelta(hours=1)

        token = get_app_token()
        upn = find_upn_by_email(token, sender_email)

        attendees_list = [
            {
                "email": addr.strip(),
                "name": addr.strip()
            } for addr in attendees.split(",") if addr.strip()
        ]

        payload = {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": body or ""
            },
            "start": {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": time_zone
            },
            "end": {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": time_zone
            },
            "location": {
                "displayName": location or "Not specified"
            },
            "attendees": [
                {
                    "emailAddress": {
                        "address": a["email"],
                        "name": a["name"]
                    },
                    "type": "required"
                } for a in attendees_list
            ]
        }

        event = graph_post(f"/users/{upn}/calendar/events", token, payload)

        return (
            f"✅ Event Created!\n"
            f"Sender: {sender_email}\n"
            f"Subject: {event['subject']}\n"
            f"Start: {event['start']['dateTime']} ({event['start']['timeZone']})\n"
            f"End: {event['end']['dateTime']} ({event['end']['timeZone']})\n"
            f"Location: {event.get('location', {}).get('displayName', 'Not specified')}\n"
            f"Attendees: {', '.join([a['emailAddress']['address'] for a in event.get('attendees', [])])}"
        )

    except Exception as e:
        return f"❌ Failed to create event: {str(e)}"
