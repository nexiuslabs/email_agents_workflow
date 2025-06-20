import os
import logging
import requests
from msal import ConfidentialClientApplication
from crewai.tools import tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_graph_app():
    return ConfidentialClientApplication(
        client_id=os.getenv("CLIENT_ID"),
        client_credential=os.getenv("CLIENT_SECRET"),
        authority=f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}"
    )

def get_access_token(app):
    token_resp = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    access_token = token_resp.get("access_token")
    if not access_token:
        raise RuntimeError("Failed to acquire Microsoft Graph access token")
    return access_token

def get_upn_by_email(email: str, access_token: str) -> str:
    url = f"https://graph.microsoft.com/v1.0/users?$filter=mail eq '{email}'&$select=userPrincipalName"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    response = requests.get(url, headers=headers)
    if not response.ok:
        raise RuntimeError(f"Failed to find UPN by email: {response.status_code} {response.text}")
    users = response.json().get("value", [])
    if not users:
        raise ValueError(f"No user found with mail = {email}")
    return users[0]["userPrincipalName"]

def fetch_user_profile(upn: str, access_token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # Fetch base user profile
    profile_url = f"https://graph.microsoft.com/v1.0/users/{upn}"
    profile_resp = requests.get(profile_url, headers=headers)
    if not profile_resp.ok:
        raise RuntimeError(f"Failed to fetch user profile: {profile_resp.status_code} {profile_resp.text}")
    user_data = profile_resp.json()

    # Try to fetch extension data
    try:
        ext_url = f"https://graph.microsoft.com/v1.0/users/{upn}/extensions/com.myApp.profileSettings"
        ext_resp = requests.get(ext_url, headers=headers)
        if ext_resp.ok:
            ext_data = ext_resp.json()
            user_data["theme"] = ext_data.get("theme", "")
            user_data["language"] = ext_data.get("language", "")
    except Exception as e:
        logger.warning(f"Extension not found or failed: {e}")

    return user_data

@tool("CrewAI: Fetch user profile by email from Graph API")
def get_user_profile_by_email(email: str) -> str:
    """
    Given a user's email, fetch their profile information (including basic details and profileSettings extension)
    from Microsoft Graph API and return it as a formatted string.
    """
    try:
        logger.info(f"👤 Fetching profile for email: {email}")
        app = get_graph_app()
        access_token = get_access_token(app)

        upn = get_upn_by_email(email, access_token)
        logger.info(f"🔑 Found UPN: {upn}")

        profile = fetch_user_profile(upn, access_token)
        logger.info(f"📄 Profile fetched for {upn}")

        profile_lines = [
            f"Display Name: {profile.get('displayName', '')}",
            f"Email: {profile.get('mail', '')}",
            f"UPN: {profile.get('userPrincipalName', '')}",
            f"Job Title: {profile.get('jobTitle', '')}",
            f"Department: {profile.get('department', '')}",
            f"Theme: {profile.get('theme', 'N/A')}",
            f"Language: {profile.get('language', 'N/A')}",
        ]
        return "\n".join(profile_lines)

    except Exception as e:
        logger.exception("❌ Error fetching user profile")
        raise RuntimeError(f"get_user_profile_by_email failed: {e}")
