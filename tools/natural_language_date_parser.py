from datetime import datetime
import dateparser
from crewai.tools import BaseTool
from typing import List, Dict


class NormalizeDueDatesTool(BaseTool):
    name: str = "normalize_due_dates"
    description: str = "Converts natural language 'due_at' dates into ISO 8601 timestamps for each task in a list."

    def _run(self, tasks: List[Dict]) -> List[Dict]:
        for task in tasks:
            if 'due_at' in task and isinstance(task['due_at'], str):
                parsed = dateparser.parse(task['due_at'], settings={'PREFER_DATES_FROM': 'future'})
                if parsed:
                    task['due_at'] = parsed.isoformat()
                else:
                    # Remove if unparseable
                    task.pop('due_at', None)
            elif task.get('due_at') is None:
                task.pop('due_at', None)
        return tasks
