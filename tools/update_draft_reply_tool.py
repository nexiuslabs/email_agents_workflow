from crewai.tools import tool
from db_utils import update_draft_reply

@tool("update_draft_reply_tool")
def update_draft_reply_tool(mail_id: str, ai_draft_reply: str) -> str:
    """
    Update the database with the AI-generated draft email reply for the given mail ID.
    """
    try:
        update_draft_reply(mail_id=mail_id, ai_draft_reply=ai_draft_reply)
        return f"Draft reply successfully updated for mail_id: {mail_id}"
    except Exception as e:
        return f"Failed to update draft reply: {str(e)}"
