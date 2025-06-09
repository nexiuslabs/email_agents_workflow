import os
import logging
from functools import lru_cache
from msal import ConfidentialClientApplication
import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from pybreaker import CircuitBreaker
from prometheus_client import Counter, Histogram
from crewai.tools import tool
from dotenv import load_dotenv

load_dotenv()

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Metrics ---
CONTACT_LOOKUP_ATTEMPTS = Counter(
    "contact_lookup_attempts_total", "Total attempts to look up contact email via Graph API", ["status"]
)
CONTACT_LOOKUP_LATENCY = Histogram(
    "contact_lookup_latency_seconds", "Latency of Graph API contact lookups", ["stage"]
)

# --- Circuit Breaker ---
contact_lookup_breaker = CircuitBreaker(fail_max=5, reset_timeout=60)

@lru_cache()
def get_graph_app() -> ConfidentialClientApplication:
    """
    Lazily load MSAL app from environment variables.
    """
    tenant_id = os.getenv("TENANT_ID")
    client_id = os.getenv("CLIENT_ID")
    client_secret = os.getenv("CLIENT_SECRET")
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    return ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority
    )

@tool("Enterprise: Look up a contact's email by name from Microsoft Graph contacts list.")
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
def get_contact_email_by_name(sender: str, contact_name: str) -> str:
    """
    Look up and return the email address of a contact (by display name)
    in the contact list of the specified sender (email address).
    Returns None if no matching contact or no alternate email found.
    """
    CONTACT_LOOKUP_ATTEMPTS.labels(status="started").inc()

    logger.info("Looking up contact email for %s", contact_name)
    
    with CONTACT_LOOKUP_LATENCY.labels(stage="acquire_token").time():
        msal_app = get_graph_app()
        token_response = msal_app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        access_token = token_response.get("access_token")
    
    if not access_token:
        err = token_response.get("error_description", "Unknown error")
        logger.error("Token acquisition failed: %s", err)
        CONTACT_LOOKUP_ATTEMPTS.labels(status="token_error").inc()
        raise RuntimeError(f"Failed to acquire token: {err}")

    url = f"https://graph.microsoft.com/v1.0/users/{sender}/contacts"
    params = {
        "$filter": f"displayName eq '{contact_name}'",
        "$select": "emailAddresses"
    }
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        with CONTACT_LOOKUP_LATENCY.labels(stage="api_call").time():
            resp = contact_lookup_breaker.call(requests.get, url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.error("Contact lookup failed: %s", resp.text)
            CONTACT_LOOKUP_ATTEMPTS.labels(status=f"http_{resp.status_code}").inc()
            raise RuntimeError(f"Graph API returned {resp.status_code}")
        
        data = resp.json()
        if not data.get("value"):  # no contacts found
            CONTACT_LOOKUP_ATTEMPTS.labels(status="not_found").inc()
            logger.info("No contact found with name: %s", contact_name)
            return None

        contact = data["value"][0]
        email_addresses = contact.get("emailAddresses", [])
        for email in email_addresses:
            if email.get("address", "").lower() != sender.lower():
                CONTACT_LOOKUP_ATTEMPTS.labels(status="success").inc()
                logger.info("Found contact email: %s", email["address"])
                return email["address"]

        # found contact but no alternate email
        CONTACT_LOOKUP_ATTEMPTS.labels(status="no_alternate_email").inc()
        logger.info("Contact '%s' has no alternate email", contact_name)
        return None

    except Exception:
        logger.exception("get_contact_email_by_name encountered exception")
        CONTACT_LOOKUP_ATTEMPTS.labels(status="exception").inc()
        raise
