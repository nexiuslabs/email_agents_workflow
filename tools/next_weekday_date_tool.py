# src/your_project/tools/next_weekday_date_tool.py

from datetime import datetime, timedelta
from crewai.tools import tool

@tool("NextWeekdayDateTool")
def next_weekday_date_tool(current_date_str: str, weekday_name: str, hour: int, minute: int) -> str:
    """
    Compute next occurrence of given weekday and time after current_date.
    
    Args:
        current_date_str: Current date string in ISO format, e.g. "2025-06-13T06:03:00"
        weekday_name: Name of weekday, e.g. "Tuesday"
        hour: Hour of target time (0-23)
        minute: Minute of target time (0-59)

    Returns:
        ISO format string for next occurrence datetime.
    """
    weekday_map = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2,
        'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6
    }
    target_weekday = weekday_map[weekday_name.lower()]
    
    current_date = datetime.fromisoformat(current_date_str)
    
    days_ahead = (target_weekday - current_date.weekday() + 7) % 7
    # if today is target weekday but time has passed, move to next week
    if days_ahead == 0 and (current_date.hour > hour or (current_date.hour == hour and current_date.minute >= minute)):
        days_ahead = 7
    
    next_date = current_date + timedelta(days=days_ahead)
    next_date = next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    return next_date.isoformat()
