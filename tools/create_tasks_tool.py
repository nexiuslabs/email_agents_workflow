from crewai.tools import tool
from db_utils import insert_new_task
import logging
import json
from dateparser import parse as parse_date
from datetime import datetime, timedelta
from typing import Optional

@tool("Create tasks from structured task list (JSON)")
def create_tasks_from_summary(summary: str, id: str, userId: int) -> str:
    """
    Parses a JSON list of task objects from a string and inserts them into the database.
    Each task must have 'title', 'detail', and optional 'due_at'.

    Args:
        summary (str): JSON string representing a list of task dicts
        id (str): Email ID
        userId (int): User ID

    Returns:
        str: Log of inserted tasks
    """
    try:
        tasks = json.loads(summary)
        if isinstance(tasks, str):  # Handles double-encoded case
            tasks = json.loads(tasks)
        if not isinstance(tasks, list):
            return "Invalid format: summary must be a list of task objects."
    except Exception as e:
        logging.exception("Invalid summary JSON")
        return f"Invalid summary JSON: {str(e)}"

    created = 0
    result_lines = []

    for task in tasks:
        # Handle possible None values safely
        title = (task.get("title") or "").strip()
        detail = (task.get("detail") or "").strip()
        raw_due_at = (task.get("due_at") or "").strip()

        # Parse date if present, otherwise default to 3 days from now
        due_at = parse_date(raw_due_at, settings={"PREFER_DATES_FROM": "future"}) if raw_due_at else datetime.now() + timedelta(days=3)

        if not (title and detail):
            result_lines.append(f"⚠️ Skipped invalid task (missing title or detail).")
            continue

        try:
            insert_new_task(
                user_id=userId,
                mail_id=id,
                title=title,
                detail=detail,
                due_at=due_at.isoformat() if due_at else None
            )
            created += 1
            result_lines.append(f"✅ Created task '{title}' (due: {due_at.strftime('%Y-%m-%d')})")
        except TypeError as te:
            logging.exception(f"❌ TypeError inserting task '{title}'. Maybe due_at isn't accepted.")
            result_lines.append(f"❌ Failed to insert task '{title}': due_at parameter not supported.")
        except Exception as e:
            logging.exception(f"❌ Failed to insert task '{title}': {str(e)}")
            result_lines.append(f"❌ Failed to insert task '{title}'")

    result_lines.append(f"\n{created} task(s) successfully created.")
    return "\n".join(result_lines)
