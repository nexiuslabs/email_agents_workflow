import os
import requests
from msal import ConfidentialClientApplication
from crewai_tools import tool

@tool("ms_graph_schedule_tool")
def get_schedule_summary() -> str:
    """Fetch working hours, calendar permissions, and next 10 events for a user via Microsoft Graph"""

    client_id = os.getenv("CLIENT_ID")
    tenant_id = os.getenv("TENANT_ID")
    client_secret = os.getenv("CLIENT_SECRET")
    user_email = os.getenv("USER_EMAIL")

    if not all([client_id, tenant_id, client_secret, user_email]):
        return "Missing required environment variables."

    # Step 1: Authenticate
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )
    token_result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in token_result:
        return "Failed to acquire token: " + str(token_result.get("error_description"))

    token = token_result["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Step 2: Get UPN
    user_url = f"https://graph.microsoft.com/v1.0/users?$filter=mail eq '{user_email}'&$select=userPrincipalName"
    res = requests.get(user_url, headers=headers).json()
    if not res.get("value"):
        return f"No user found with email {user_email}"

    upn = res["value"][0]["userPrincipalName"]

    try:
        # Step 3: Working hours
        working_time = requests.get(
            f"https://graph.microsoft.com/v1.0/users/{upn}/calendar/workingHours",
            headers=headers,
        ).json()

        # Step 4: Permissions
        permissions_raw = requests.get(
            f"https://graph.microsoft.com/v1.0/users/{upn}/calendar/calendarPermissions",
            headers=headers,
        ).json()

        permissions = []
        for p in permissions_raw.get("value", []):
            permissions.append(f"{p.get('emailAddress', {}).get('address', 'N/A')} - Role: {p.get('role')}")

        # Step 5: Events
        events_raw = requests.get(
            f"https://graph.microsoft.com/v1.0/users/{upn}/calendar/events?$top=10",
            headers=headers,
        ).json()

        events = []
        for e in events_raw.get("value", []):
            events.append(f"{e['subject']} ({e['start']['dateTime']} - {e['end']['dateTime']}) at {e.get('location', {}).get('displayName', 'N/A')}")

        # Final result summary
        return (
            f"📅 Working Time:\n{working_time}\n\n"
            f"🔐 Permissions:\n" + "\n".join(permissions) + "\n\n"
            f"🗓 Upcoming Events:\n" + "\n".join(events)
        )

    except Exception as e:
        return f"Error fetching schedule data: {str(e)}"
