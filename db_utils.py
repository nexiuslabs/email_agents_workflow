import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi import UploadFile
import requests
import urllib.parse
from typing import Optional

load_dotenv()  # This will load the variables from .env into the environment

# Database URL must be set in the environment
# Example: export DATABASE_URL="postgresql://<user>:<password>@68.183.184.3:5432/<dbname>"

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("Environment variable DATABASE_URL is not set")

def get_connection():
    """
    Establish a new database connection using the DATABASE_URL.
    """
    return psycopg2.connect(DB_URL)

def get_or_create_user(name: str, email: str = None) -> int:
    """
    Find an existing user by name/email or create one if not found.
    Returns the user_id.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM users WHERE name = %s AND email IS NOT DISTINCT FROM %s",
                (name, email),
            )
            row = cur.fetchone()
            if row:
                return row[0]

            cur.execute(
                "INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id",
                (name, email),
            )
            user_id = cur.fetchone()[0]
            conn.commit()
            return user_id
    finally:
        conn.close()

def get_user_id_by_email(email: str) -> int:
    """
    Retrieve the user_id associated with the given email.
    Returns None if no user is found.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if row:
                return row[0]
    finally:
        conn.close()

def create_conversation(user_id: int) -> int:
    """
    Start a new conversation for the given user.
    Returns the conversation_id.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (user_id, last_message_at) VALUES (%s, NOW()) RETURNING id",
                (user_id,),
            )
            conversation_id = cur.fetchone()[0]
            conn.commit()
            return conversation_id
    finally:
        conn.close()


def insert_message(conversation_id: int, is_user: bool, content: str, file_urls: str = None) -> None:
    """
    Insert a message into the messages table and update the conversation's last_message_at.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (conversation_id, is_user, content, file_urls) VALUES (%s, %s, %s, %s)",
                (conversation_id, is_user, content, file_urls),
            )
            # keep track of when the conversation last saw activity
            cur.execute(
                "UPDATE conversations SET last_message_at = NOW() WHERE id = %s",
                (conversation_id,),
            )
            conn.commit()
    finally:
        conn.close()


def insert_reply_message(conversation_id: int, is_user: bool, content: str) -> None:
    """
    Insert a message into the messages table and update the conversation's last_message_at.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (conversation_id, is_user, content) VALUES (%s, %s, %s)",
                (conversation_id, is_user, content),
            )
            # keep track of when the conversation last saw activity
            cur.execute(
                "UPDATE conversations SET last_message_at = NOW() WHERE id = %s",
                (conversation_id,),
            )
            conn.commit()
    finally:
        conn.close()

def record_qa(conversation_id: int, question: str, answer: str, file_urls: str = None) -> int:
    """
    Convenience function to record a QA interaction end-to-end:
      1. Ensures the user exists (or creates them).
      2. Creates a new conversation.
      3. Inserts the user's question and agent's answer.

    Returns:
        The new conversation_id.
    """

    # record the back-and-forth
    insert_message(conversation_id, True, question, file_urls)
    insert_message(conversation_id, False, answer, file_urls) 
    update_conversation_title(conversation_id, question)
    return conversation_id



'''
if __name__ == "__main__":
    record_qa(conversation_id=1, question="Hello AI?", answer="Hello! How can I help you today?")
'''
def update_conversation_title(conversation_id: int, question: str) -> None:
    """
    Given a conversation_id and question, checks if the message is the first one in the conversation and if so,
    updates the conversation title in the conversations table.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # count the number of messages in the conversation
            cur.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = %s", (conversation_id,))
            row = cur.fetchone()
            print(row)
            if row[0] == 2:
                cur.execute("UPDATE conversations SET title = %s WHERE id = %s", (question, conversation_id))
                conn.commit()
    finally:
        conn.close()

# ========= File Upload =========
FILE_SERVER_BASE = os.getenv("FILE_SERVER_BASE_URL", "https://api.nexiuslabs.com")

def upload_file(folder_path: str, file: UploadFile) -> str:
    """
    Uploads a file to your file‐server.
    folder_path: e.g. "/uploads" or "uploads"
    file: the FastAPI UploadFile
    Returns the full URL where the file now lives.
    """
    # normalize folder_path
    folder = folder_path.lstrip("/")
    # build a full URL safely
    upload_url = urllib.parse.urljoin(FILE_SERVER_BASE.rstrip("/") + "/", f"{folder}/{file.filename}")
    # rewind and read
    file.file.seek(0)
    data = file.file.read()
    resp = requests.put(upload_url, data=data, stream=True)
    # raise on any HTTP error
    resp.raise_for_status()
    return upload_url

#------------------------ TASK ---------------------------

def get_tasks_by_user_id(user_id: int):
    """
    Retrieve all tasks for a given user_id from the Tasks table.
    Returns a list of dicts.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, user_id, mail_id, title, detail, due_at, is_done, created_at
                FROM Tasks
                WHERE user_id = %s
                ORDER BY due_at ASC NULLS LAST, created_at DESC
            """, (user_id,))
            return cur.fetchall()
    finally:
        conn.close()

def insert_new_task(user_id: int, mail_id: str, title: str, detail: str, due_at: Optional[str] = None) -> int:
    """
    Insert a new task into the Tasks table.
    Returns the id of the new task.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if due_at:
                cur.execute("""
                    INSERT INTO Tasks (user_id, mail_id, title, detail, due_at, created_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    RETURNING id
                """, (user_id, mail_id, title, detail, due_at))
            else:
                cur.execute("""
                    INSERT INTO Tasks (user_id, mail_id, title, detail, created_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    RETURNING id
                """, (user_id, mail_id, title, detail))
            row = cur.fetchone()
            conn.commit()
            return row[0]
    finally:
        conn.close()

def update_task_status(task_id: int, is_done: bool) -> None:
    """
    Update the is_done status of a task with given id.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE Tasks
                SET is_done = %s
                WHERE id = %s
            """, (is_done, task_id))
            conn.commit()
    finally:
        conn.close()

def list_tasks_by_user_id(user_id: int):
    """
    Retrieve all tasks for a given user_id from the Tasks table.
    Returns a list of dicts.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, user_id, mail_id, title, detail, due_at, is_done, created_at
                FROM Tasks
                WHERE user_id = %s
                ORDER BY due_at ASC NULLS LAST, created_at DESC
            """, (user_id,))
            return cur.fetchall()
    finally:
        conn.close()

def get_mail_id_by_task_id(task_id: int) -> int:
    """
    Retrieve mail_id associated with a given task_id from the Tasks table.
    Returns None if no task is found.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT mail_id FROM Tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
            if row:
                return row[0]
    finally:
        conn.close()


#=========== Email Table ===============
def insert_email(user_id: int, mail_id: str, subject: str, body_summary: str, sender: str) -> int:
    """
    Insert a new email into the Emails table.
    Returns the id of the new email.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO email_records (user_id, mail_id, subject, body_summary, sender)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (user_id, mail_id, subject, body_summary, sender))
            row = cur.fetchone()
            conn.commit()
            return row[0]
    finally:
        conn.close()