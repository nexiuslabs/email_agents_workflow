# src/project_name/tools/reminder_tool.py
import os
import requests
import msal
from crewai.tools import tool

GRAPH_API_ENDPOINT = 'https://graph.microsoft.com/v1.0'

def get_app_token():
    app = msal.ConfidentialClientApplication(
        client_id=os.getenv('CLIENT_ID'),
        client_credential=os.getenv('CLIENT_SECRET'),
        authority=f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}"
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

    if 'access_token' not in result:
        raise Exception("Could not acquire access token")

    return result['access_token']

def find_upn_by_email(token, email):
    url = f"{GRAPH_API_ENDPOINT}/users?$filter=mail eq '{email.replace('\'', '\'\'')}'&$select=userPrincipalName"
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    data = res.json()

    if not data.get('value'):
        raise Exception(f"No user found with mail={email}")

    return data['value'][0]['userPrincipalName']

def fetch_todo_task_lists(token, upn):
    url = f"{GRAPH_API_ENDPOINT}/users/{upn}/todo/lists"
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    return res.json().get('value', [])

def get_default_task_list_id(token, upn):
    lists = fetch_todo_task_lists(token, upn)
    default_list = next((l for l in lists if l.get('wellknownListName') == 'defaultList'), None)
    if default_list:
        return default_list['id']
    elif lists:
        return lists[0]['id']
    else:
        raise Exception(f"No To Do lists found for user {upn}")

def create_todo_task(token, upn, list_id, title, body=None, due_date_time=None):
    url = f"{GRAPH_API_ENDPOINT}/users/{upn}/todo/lists/{list_id}/tasks"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "title": title,
        "body": {
            "content": body or "",
            "contentType": "text"
        }
    }

    if due_date_time:
        payload["dueDateTime"] = {
            "dateTime": due_date_time,
            "timeZone": "UTC"
        }

    res = requests.post(url, json=payload, headers=headers)
    res.raise_for_status()
    task = res.json()

    return {
        "title": task.get("title"),
        "body": task.get("body", {}).get("content", ""),
        "due_date": task.get("dueDateTime", {}).get("dateTime")
    }

@tool("create_todo_task")
def create_todo_task_tool(email: str, task_title: str, task_body: str = "", due_date_time: str = None) -> str:
    """
    Creates a Microsoft To Do task for the given user email.

    Args:
    - email: User's email address
    - task_title: Title of the To Do task
    - task_body: (Optional) Body content of the task
    - due_date_time: (Optional) Due date in format "YYYY-MM-DDTHH:MM:SS"

    Returns: Stringified JSON with task details.
    """
    token = get_app_token()
    upn = find_upn_by_email(token, email)
    list_id = get_default_task_list_id(token, upn)
    task = create_todo_task(token, upn, list_id, task_title, task_body, due_date_time)

    return f"Created task: Title='{task['title']}', Body='{task['body']}', DueDate='{task['due_date']}'"
