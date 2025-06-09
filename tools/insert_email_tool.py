# insert_email_tool.py

from crewai.tools import tool
from db_utils import insert_email
import logging

logger = logging.getLogger(__name__)

@tool("Insert email record into database")
def insert_email_record(summary: str, id: str, userId: int, subject: str, sender: str) -> str:
    """
    Inserts a summarized email into the Emails table.

    Args:
        summary (str): The 2–3 sentence summary of the email body.
        id (str): The original mail_id (e.g., Graph ID) of the email.
        userId (int): The internal user ID.
        subject (str): The email subject line.
        sender (str): The sender's email address.

    Returns:
        str: A confirmation message indicating success or failure.
    """
    # Basic validation
    if not all([summary, id, userId, subject, sender]):
        return "⚠️ Missing required fields: summary, id, userId, subject, or sender."

    try:
        # Insert into the Emails table
        new_id = insert_email(
            user_id=userId,
            mail_id=id,
            subject=subject,
            body_summary=summary,
            sender=sender,
        )
        return f"✅ Inserted email record with ID {new_id}."
    except Exception as e:
        logger.exception(f"❌ Failed to insert email record for mail_id '{id}'")
        return f"❌ Error inserting email record: {str(e)}"
