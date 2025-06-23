import logging
from crewai import Agent, Task, Crew, Process
from tools.create_tasks_tool import create_tasks_from_summary
from tools.natural_language_date_parser import NormalizeDueDatesTool
from tools.send_email_tool import send_email
from tools.get_receiver_email_tool import get_contact_email_by_name
from tools.read_email_tool import read_email_by_task_id
from tools.insert_email_tool import insert_email_record
from tools.fetch_email_thread_tools import fetch_email_thread_tool
from tools.getprofile_tool import get_user_profile_by_email
from tools.reminder_task_tool import create_todo_task_tool
from tools.create_calendar_event_tool import create_calendar_event_tool
from tools.next_weekday_date_tool import next_weekday_date_tool
from tools.reply_email_tool import reply_to_latest_email
from tools.get_last_recipient_message_tool import get_last_recipient_message_tool
from tools.update_draft_reply_tool import update_draft_reply_tool
# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Agents ---
agent_manager = Agent(
    role=" Email Controller and Router",
    goal=" Ensure only relevant, actionable emails are routed to the appropriate agent, while ignoring noise.",
    backstory="You are the intelligent gatekeeper of the system. Every incoming email first passes through you."
    "With a combination of heuristic rules and smart judgment, you identify which emails are worth acting on"
    "and which ones can be safely ignored. You prevent information overload by filtering out newsletters,"
    "automated messages, and spam. Only emails that require human action are routed to the appropriate agent.",
    memory=False,
    verbose=False,
    llm_config={"model": "gpt-4o-mini", "temperature": 0.2},
    allow_delegation=False,
)

filter_and_route_email_task = Task(
    description=(
        "Given an incoming email event, determine whether it requires human action."
        "Use heuristics (e.g., sender domain, subject line keywords like 'newsletter', etc.) and/or AI classification"
        "to decide if the email is important and actionable."
        
        "- If the email is non-actionable (e.g., a newsletter or automated message), discard it."
        "- If the email seems important and requires a response or human input, route it to the HumanActionAgent."
        "Respect rate limits for how frequently AI models are called."
        "Your job is to act as a smart filter and router for the incoming emails."
    ),
    expected_output="One of:\n"
    "- Dropped: Non-actionable\n"
    "- Routed to HumanActionAgent: [brief reasoning why it's important]",
    agent=agent_manager,
    input_keys=["summary", "id", "userId"]  # ✅ All are passed as input keys
)


human_action_agent = Agent(
    role="Email Intelligence Agent",
    goal="Analyze user input and extract actionable tasks with due dates, classify email type, and discard non-actionable ones.",
    backstory="You're an elite assistant AI trained to comb through executive emails. With sharp insight, you turn requests into actionable items, identify deadlines, and make sure no important task goes unnoticed.",
    memory=False,
    verbose=False,
    llm_config={"model": "gpt-4o-mini", "temperature": 0.2},
    allow_delegation=False,
)


analyze_email_task = Task(
    description=(
        """You are given an email and must identify and summarize any human-actionable items in it.

Email:
- mail_id: {id}
- received: {receivedDateTime}
- subject: {subject}
- preview: {bodyPreview}
- from: {sender}

Instructions:
- Read the email content carefully.
- Extract and clearly summarize all human-actionable tasks mentioned or implied in the message.
- Focus only on what the recipient is being asked or expected to do.
- Return your answer as a JSON object containing:
  - "summary": the numbered list of tasks as a single string.
  - "id": the `mail_id` value passed to you.
  - "userId": the `userId` value passed to you. DO NOT infer this — just reuse the value.

Example:
{
  "summary": "1. Do the thing\n2. Submit report",
  "id": "{id}",
  "userId": {userId}
}

If there is nothing actionable, return:
{
  "summary": "No human action is required.",
  "id": "{id}",
  "userId": {userId}
}
"""
    ),
    expected_output="JSON with keys: summary, id, userId",
    agent=human_action_agent,
    input_keys=["id", "receivedDateTime", "subject", "bodyPreview", "sender", "userId"]
)


task_manager_agent = Agent(
    role="Task Manager",
    goal="Save human-actionable tasks extracted from emails into the database.",
    backstory=(
        "You're responsible for taking summaries of human tasks extracted from emails and saving them as tasks with title, detail, due dates in our system. "
        "You ensure that every actionable item is stored for follow-up."
    ),
    tools=[NormalizeDueDatesTool(), create_tasks_from_summary],
    memory=False,
    verbose=True,
    allow_delegation=False,
    llm_config={"model": "gpt-4o-mini", "temperature": 0.2}
)


create_task_records_task = Task(
    description=(
        "Create tasks in the database from the given summary. First normalize any natural language dates "
        "like 'this Friday' using `normalize_due_dates`, then call `create_tasks_from_summary` with this structure:\n"
        "{\n"
        "  'summary': <summary>,\n"
        "  'id': <id>,\n"
        "  'userId': <userId>\n"
        "}"
    ),
    expected_output=" A JSON list where each item contains: 'title', 'detail', and 'due_at' in ISO 8601 if available.",
    agent=task_manager_agent,
    input_keys=["summary", "id", "userId"]
)


#=============== Email Reviewer ================

email_review_agent = Agent(
    role="Email Reviewer",
    goal="Ensure emails meet Nexius Labs quality standards.",
    backstory="You carefully review every email for structure, tone, and clarity.",
    memory=True,
    verbose=True,
    llm_config={"model": "gpt-o4-mini", "temperature": 0.2},
)

receiver_email_lookup_agent = Agent(
    role="Receiver Email Lookup Agent",
    goal="Validate or look up the recipient's email before proceeding.",
    backstory="You verify the recipient's email or fetch it by contact name using Microsoft Graph.",
    tools=[get_contact_email_by_name],
    memory=False,
    verbose=True,
    llm_config={"model": "gpt-o4-mini", "temperature": 0.2},
    allow_delegation=False,
)

#=============== Email Data Extractor ================

email_data_extractor_agent = Agent(
    role="Email Data Extractor",
    goal="Extract structured email fields from reviewed email content.",
    backstory="You're a parsing expert that can pull out structured fields like sender, receiver, subject, and content from reviewed, formatted email content.",
    memory=False,
    verbose=True,
    llm_config={"model": "gpt-o4-mini", "temperature": 0.2},
    allow_delegation=False,
)

#=============== Email Sender ================

email_sender_agent = Agent(
    role="Email Sender",
    goal="Send approved emails using Microsoft Graph API securely and reliably.",
    backstory="Handles final delivery of polished and validated emails via enterprise APIs.",
    tools=[send_email],
    memory=False,
    verbose=True,
    llm_config={"model": "gpt-o4-mini", "temperature": 0.2},
    allow_delegation=False,
)

send_email_task = Task(
    description="Send the finalized email via Microsoft Graph API. Include attachments from the context if any.",
    expected_output="Confirmation message with email details or error.",
    input_keys=["receiver", "sender", "subject", "content"],
    context_keys=["attachments"],  # ✅ This is the missing link
    agent=email_sender_agent,
)

email_sender_data_extractor_agent = Agent(
    role="Email Sender Data Extractor",
    goal="Extract receiver, subject, and body fields from formatted email content.",
    backstory="You are a parsing and extraction expert specialized in processing formatted email text."
    "Your goal is to accurately extract key email fields and return them in a structured JSON format."
    "You must handle various formatting styles and always ensure clean and precise extraction.",
    tools=[send_email],
    memory=False,
    verbose=True,
    llm_config={"model": "gpt-o4-mini", "temperature": 0.2},
    allow_delegation=False,
)

extract_email_sender_fields_task = Task(
    description=""" Given a formatted {question} string and {sender_email} string, extract the following fields:
    - sender → Extract from line starting with "From : "
    - receiver → Extract from line starting with "To : "
    - subject → Extract from line starting with "Subject : "
    - body → All text after the subject line and blank line(s).

    The final result MUST be valid JSON in this exact structure:

    {
      "sender": "...",
      "receiver": "...",
      "subject": "...",
      "body": "..."
    }

    Your final answer MUST be ONLY the JSON object, with no additional commentary.

    Here is the formatted question and sender email:

    {question}
    {sender_email}""",
    expected_output="A valid JSON object with sender, receiver, subject, and body fields.",
    input_keys=["sender_email","question"],
    output_keys=["sender", "receiver", "subject", "body"],
    agent=email_sender_agent,
)

#=========== Conversation ============

email_support_conversation_agent = Agent(

    role="Customer Support Email Assistant",
    goal=(
        "Provide helpful, natural, and polite responses to questions related to email communication "
        "and general customer support conversation (e.g. availability, greetings, gratitude). "
        "Respond conversationally when appropriate. Politely decline if the topic is not relevant."
    ),
    backstory=(
        "You are a smart, friendly assistant who specializes in helping users with writing professional emails in 24/7 "
        "and engaging in light, polite customer support conversation. "
        "You're designed to feel human, understand common phrasing, and provide short but useful answers. "
        "If something is outside your expertise (e.g. math, tech, personal help), you gently decline."
    ),
    memory=True,
    verbose=False,
    llm_config={"model": "gpt-4o", "temperature": 0.6},
    allow_delegation=False,
)



#============ Draft Writer Agent and Task =============

email_drafting_agent = Agent(
    role="Email Drafter and Formatter",
    goal="Create a properly structured professional email based on user instruction and recipient profile.",
    backstory=(
        "You're a communication specialist for Nexius Labs. "
        "You write highly personalized professional emails by first learning about the recipient using their profile. "
        "You adapt tone, content, and style accordingly."
    ),
    memory=True,
    verbose=True,
    tools=[get_user_profile_by_email],   # <-- adding your tool!
    llm_config={"model": "gpt-4o-mini", "temperature": 0.3},
)

process_email_task = Task(
    description="""
From the following user instruction, do the following:
1. Extract the recipient's name or email.
2. Use the get_user_profile_by_email tool to fetch the sender's profile by their email (sender_email).
3. Analyze the profile to understand their persona — e.g. role, seniority, department, interests (theme, language).
4. Infer an appropriate subject line from the context and profile.
5. Write a professional business email body using Nexius Labs' tone and adapted to the sender's persona.
6. Ensure the message ends with a signature that includes the sender's name (parsed from sender_email) and the company name 'Nexius Labs'.


Signature format example:
Best regards,  
John Doe  
Program Coordinator
Nexius Labs

If you can't infer the name from the sender_email, use:
Best regards,  
Nexius Labs Team

Input:
User instruction: {question}  
Sender Email: {sender_email}

Return a **valid JSON**:
{
  "receiver": "<recipient name or email>",
  "subject": "<email subject>",
  "content": "<email body with signature>",
  "sender": "{sender_email}",
  "job_title": "<job title>",
  "attachments": "<list of attachments>"
}
""",
    expected_output="Valid JSON with receiver, subject, content, and sender.",
    input_keys=["question", "sender_email"],
    output_keys=["receiver", "subject", "content", "sender", "attachments"],
    agent=email_drafting_agent
)


lookup_receiver_email_task = Task(
    description="""
If 'receiver' is not an email address, look up the real email using the contact tool.
Return updated JSON or error JSON if not found.
""",
    expected_output="Updated JSON or error JSON",
    input_keys=["receiver", "sender", "subject", "content"],
    output_keys=["receiver", "sender", "subject", "content"],
    agent=receiver_email_lookup_agent,
)

review_email_task = Task(
    description="""
Check email draft for correctness, structure, tone, and missing fields. Return original or correction feedback.
""",
    expected_output="If valid, return JSON; if not, return detailed feedback for redrafting.",
    input_keys=["receiver", "sender", "subject", "content"],
    output_keys=["receiver", "sender", "subject", "content"],
    agent=email_review_agent,
)


restore_attachments_task = Task(
    description="Re-attach the originally uploaded attachments to the finalized email before sending. Do not change or generate attachments.",
    expected_output="Structured email JSON with original attachments added back in.",
    input_keys=["receiver", "sender", "subject", "content", "attachments"],
    output_keys=["receiver", "sender", "subject", "content", "attachments"],
    context_keys=["attachments"],  # ✅ pulled from kickoff context
    agent=email_sender_agent,
    async_execution=False
)

extract_email_fields_task = Task(
    description="""
Take the reviewed email content and extract the following fields:
- sender
- receiver
- subject
- content

Return the result as valid **JSON** with this structure:
{
  "sender": "...",
  "receiver": "...",
  "subject": "...",
  "content": "..."
}
""",
    expected_output="Valid JSON with sender, receiver, subject, content.",
    input_keys=["receiver", "sender", "subject", "content"],
    output_keys=["sender", "receiver", "subject", "content"],
    agent=email_data_extractor_agent
)



email_conversation_task = Task(
    description=(
        "You're a smart, human-like assistant who only reply warmly and briefly to casual user messages.."
        "If it's a question outside your scope (e.g., math, trivia, news, programming, general info), do NOT redirect. Instead:\n"
        "    - Politely decline.\n"
        "    - Say you are not able to answer that because your focus is only on email writing and communication.\n"
        "    - Use natural, varied language. Do NOT say the same thing every time.\n\n"
        "Make sure your response is always clear, honest, brief, and polite."
    ),
    expected_output="A polite, direct, natural-sounding sentence — answering clearly or gracefully declining.",  
    agent=email_support_conversation_agent
)


#=============== Draft Reply ================

draft_reply_agent = Agent(
    role="Email Draft Specialist",
    goal="Generate accurate, professional, and context-appropriate email replies.",
    backstory="You are a language-savvy assistant with exceptional written communication skills. "
              "Your purpose is to streamline email communication by drafting high-quality responses "
              "that maintain proper tone, structure, and clarity.",
    memory=False,
    verbose=True,
    tools=[fetch_email_thread_tool, get_user_profile_by_email, get_last_recipient_message_tool],
    llm_config={"model": "gpt-4o-mini", "temperature": 0.2},
    allow_delegation=False,
)

generate_email_reply_task = Task(
    description="""
Your objective is to draft a professional and context-aware reply to the most recent message in an email thread.

Steps:

1. Use the `fetch_email_thread_by_task_id_tool` to get the entire email thread.
   Input: {"mail_id": {mail_id}, "sender_email": {sender_email}}

2. Use `get_last_recipient_message_tool` with:
   Input: {"user_email": {sender_email}, "mail_id": {mail_id}}
   to isolate the last message in the thread not sent by the user.

3. Use `get_user_profile_by_email`:
   Input: {"email": {sender_email}}  
   Extract details like: Display Name, Job Title, Department, Theme, and Language to adapt the tone of the reply.

4. Analyze the conversation thread (for full context) and the latest recipient message (for precise content reference).

5. Generate a reply that:
   - Starts with a polite greeting.
   - Responds accurately to the last message's content.
   - Maintains professional and appropriate tone based on the user's profile.
   - Is clear, well-structured, and courteous.

End the reply with:

Best regards,  
Henry

Your final answer MUST be only the full plain text email reply.
""",
    input_keys=["mail_id", "sender_email"],
    expected_output="Plain text reply email.",
    agent=draft_reply_agent
)


#=============== Auto Draft Reply ================

auto_draft_reply_agent = Agent(
    role="Email Draft Specialist",
    goal="Generate accurate, professional, and context-appropriate email replies.",
    backstory="You are a language-savvy assistant with exceptional written communication skills. "
              "Your purpose is to streamline email communication by drafting high-quality responses "
              "that maintain proper tone, structure, and clarity.",
    memory=False,
    verbose=True,
    tools=[fetch_email_thread_tool, get_user_profile_by_email, get_last_recipient_message_tool, update_draft_reply_tool],
    llm_config={"model": "gpt-4o-mini", "temperature": 0.2},
    allow_delegation=False,
)

auto_email_draft_reply_task = Task(
    description="""
Your objective is to draft a professional and context-aware reply to the most recent message in an email thread.

Steps:

1. Use the `fetch_email_thread_by_task_id_tool` to get the entire email thread.
   Input: {"mail_id": {mail_id}, "sender_email": {receiver}}

2. Use `get_last_recipient_message_tool` with:
   Input: {"user_email": {receiver}, "mail_id": {mail_id}}
   to isolate the last message in the thread not sent by the user.

3. Use `get_user_profile_by_email`:
   Input: {"email": {receiver}}  
   Extract details like: Display Name, Job Title, Department, Theme, and Language to adapt the tone of the reply.

4. Analyze the conversation thread (for full context) and the latest recipient message (for precise content reference).

5. Generate a reply that:
   - Starts with a polite greeting.
   - Responds accurately to the last message's content.
   - Maintains professional and appropriate tone based on the user's profile.
   - Is clear, well-structured, and courteous.

End the reply with:

Best regards,  
Henry

Your final answer MUST be only the full plain text email reply.
6. Use `update_draft_reply_tool` to update the draft reply in the database.
Input: {"mail_id": {mail_id}, "ai_draft_reply": <the generated reply>}
""",
    input_keys=["mail_id", "receiver"],
    expected_output="Plain text reply email.",
    agent=auto_draft_reply_agent
)



#============== Email Format Agent ===============

email_format_agent = Agent(
    role="Email Format Specialist",
    goal=" Convert structured email JSON data into a properly formatted email content string.",
    backstory=" You are an expert at taking structured data (like JSON) and transforming it into professional, clearly formatted email content.You always ensure the formatting is precise and matches the requested style.",
    memory=False,
    verbose=True,
    llm_config={"model": "gpt-o4-mini", "temperature": 0.2},
    allow_delegation=False,
)

email_format_task = Task(
    description="""
   Given the following fields extracted from an email:


    - receiver
    - subject
    - content

    Format them into a clean email string following this exact format:



    To : receiver,

    Subject : subject,

    content

    Use two line breaks between the header (From/To/Subject) and the email body.

    Your final answer MUST be only the formatted email string exactly matching the above format, with no extra commentary.

    """,
    expected_output="A string of the formatted email exactly matching the provided format.",
    input_keys=["receiver", "subject", "content"],
    output_keys=["formatted_email"],
    agent=email_format_agent
)

# ────────────────────────────────────────────────────────────────────────────────
# 2) Email Reader Agent and Task
# ────────────────────────────────────────────────────────────────────────────────

email_reader_agent = Agent(
    role="Email Reader",
    goal="Fetch the full email body, subject, sender, timestamp, and attachments given a mail ID.",
    backstory=(
        "You are a utility agent that reads emails via Microsoft Graph. "
        "When given a mail_id and user_id, you call the `read_email_by_task_id` tool "
        "to retrieve the complete email contents."
    ),
    memory=False,
    verbose=False,
    tools=[read_email_by_task_id],
    llm_config={"model": "gpt-o4-mini", "temperature": 0.0},
    allow_delegation=False,
)

read_email_task = Task(
    description="""
Use the `read_email_by_task_id` tool to fetch the full email.
Input: {"mail_id": <Graph Email ID>, "user_id": <internal user ID>}
Output: A dictionary with keys:
    {
      "subject": <string>,
      "body": <string>,
      "sender": <string>,
      "timestamp": <ISO8601 string>,
      "attachments": <list of attachment metadata>
    }
""",
    input_keys=["mail_id", "user_id"],
    expected_output="Dictionary containing full email content.",
    agent=email_reader_agent
)



# ────────────────────────────────────────────────────────────────────────────────
# 3) Reminder Agent and Task
# ────────────────────────────────────────────────────────────────────────────────

reminder_agent = Agent(
    role="Reminder Agent",
    goal="Classify whether a user's request is for a TODO task or a Calendar Event.",
    backstory="""You are an intelligent router agent. 
    Given a question, your job is to classify whether it is asking to create:
    - a personal task or reminder → 'todo'
    - a calendar event → 'event'
    You only output one of these two labels: 'todo' or 'event'.""",
    memory=False,
    verbose=False,
    tools=[],  # no tools, pure classification
    llm_config={"model": "gpt-4o-mini", "temperature": 0},
    allow_delegation=False,
)

reminder_task = Task(
    description="""
    Given the following question: {question}

    Classify it into one of these two categories:
    - 'todo' → if the question is about creating a personal task or reminder
    - 'event' → if the question is about creating a calendar event with time, date, attendees, or location.

    Your final answer MUST be ONLY the string 'todo' or 'event' — no explanations, no other text.
    """,
    input_keys=["question"],
    expected_output="'todo' or 'event'",
    agent=reminder_agent
)


#============ Reminder Todo Task =============

reminder_todo_formatter_preview_agent = Agent(
    role="TODO Task Formatter Preview Agent",
    goal="Extract task details from natural language input and format a human-friendly preview.",
    backstory="""You are a helpful assistant specialized in taking natural language questions for personal tasks
    and turning them into a clean, readable TODO preview.

    You will:
    - Extract the main action as Title
    - Infer any implied additional details as Body (if none are provided, leave blank or a reasonable default)
    - Determine Due Date — if a date is mentioned, parse it and format it as 'YYYY-MM-DD HH:MM UTC'; if no date is mentioned, use today's date in UTC.

    You will format the extracted data as:
    Title: <title>
    Body: <body>
    Due Date: <due_date_time formatted>
    """,
    memory=False,
    verbose=False,
    tools=[],  # No tools needed
    llm_config={"model": "gpt-4o-mini", "temperature": 0},
    allow_delegation=False,
)


reminder_todo_formatter_preview_task = Task(
    description="""
    Today's date is: {current_date}

    Given the following inputs:
    - sender: {sender}
    - question: {question}

    You will extract the following:
    - Title: the main action the user wants to do
    - Body: any additional implied details or instructions. If none are provided, infer a helpful description.
    - Due Date: if a date is mentioned, parse it and format it as 'YYYY-MM-DD HH:MM UTC';
      if no date is mentioned, use today's date ({current_date}).

    Your final output MUST be exactly in this text format:

    Title: <title>
    Body: <body>
    Due Date: <due_date_time formatted as 'YYYY-MM-DD HH:MM UTC'>

    No extra explanations or text — just the formatted preview.
    """,
    input_keys=["sender", "question", "current_date"],
    expected_output="A human-friendly preview in the required text format.",
    agent=reminder_todo_formatter_preview_agent
)


reminder_todo_agent = Agent(
    role="Personal Task Extraction and Reminder Agent",
    goal="Help users remember important tasks by extracting structured task details from their natural language questions and creating reminders in Microsoft To Do.",
    backstory="You are a helpful assistant integrated with Microsoft Graph API. You specialize in understanding natural language requests for task creation. "
    "You will parse the input question and extract:"
    "- task_title (what should be done)"
    "- task_body (if there is more detail implied)"
    "- due_date_time (if not provided, use today's date)"
    "- email (from sender)"
    "After extracting this information, you will call the create_todo_task tool.",
    memory=False,
    verbose=False,
    tools=[create_todo_task_tool],
    llm_config={"model": "gpt-4o-mini", "temperature": 0.2},
    allow_delegation=False,
)

reminder_todo_task = Task(
    description="""
     Given the {sender} and a natural language {question}, extract the following fields:
    - email: the sender
    - task_title: the main action the user wants to do
    - task_body: any optional details implied in the question
    - due_date_time: if a date is mentioned, convert it to ISO format YYYY-MM-DDTHH:MM:SS in UTC timezone.  if no date is mentioned, use today's date ({current_date}).

    After extracting the above fields, call the create_todo_task tool with them.

    Your final answer MUST be a confirmation string showing which task was created and its due date.
    """,
    input_keys=["sender", "question", "current_date"],
    expected_output="Confirmation message including created task title and due date.",
    agent=reminder_todo_agent
)

#============ Reminder Event Task =============

event_formatter_preview_agent = Agent(
    role="Event Formatter Preview Agent",
    goal="Extract event details from natural language input and format a human-friendly preview.",
    backstory="""You are an expert assistant who understands event descriptions provided in natural language.

    You will extract the following event details:
    - Subject
    - Start DateTime (in 'YYYY-MM-DD HH:MM' format + Timezone)
    - End DateTime (in 'YYYY-MM-DD HH:MM' format + Timezone)
    - Location
    - Attendees (comma-separated list of email addresses)

    You will output these fields in a human-friendly formatted text preview.
    """,
    memory=False,
    verbose=False,
    tools=[],  # No tools, pure formatting
    llm_config={"model": "gpt-4o-mini", "temperature": 0},
    allow_delegation=False,
)
event_formatter_preview_task = Task(
    description="""
    Given the following inputs:
    - sender: {sender}
    - question: {question}

    You will extract the following event details:
    - Subject: the name or title of the event
    - Start DateTime: in 'YYYY-MM-DD HH:MM' format plus the provided timezone
    - End DateTime: in 'YYYY-MM-DD HH:MM' format plus the provided timezone
    - Location: the location where the event will take place
    - Timezone: local timezone
    - Attendees: comma-separated list of email addresses of attendees

    Your final output MUST be exactly in this text format:

    Subject: <subject>
    Start DateTime: <start_datetime + timezone>
    End DateTime: <end_datetime + timezone>
    Timezone: <timezone>
    Location: <location>
    Attendees: <attendee1>, <attendee2>, ...

    No extra explanations or text — just the formatted preview.
    """,
    input_keys=["sender", "question"],
    expected_output="A human-friendly preview of the event in the required text format.",
    agent=event_formatter_preview_agent
)

reminder_event_agent = Agent(
    role="Reminder Event Agent",
    goal="  Extract calendar event details from user input and create the event in Microsoft 365 Calendar.",
    backstory=""" You are an expert assistant capable of parsing natural language inputs for calendar events.
    You understand dates, times, locations, and attendees, and can seamlessly create calendar events on behalf of the user.
    """,
    memory=False,
    verbose=False,
    tools=[create_calendar_event_tool,next_weekday_date_tool],
    llm_config={"model": "gpt-4o-mini", "temperature": 0.2},
    allow_delegation=False,
)

from tzlocal import get_localzone_name

local_tz = get_localzone_name()

reminder_event_task = Task(
    description="""
description: |
  You will receive a {question} describing an event (such as a meeting or appointment) and - {sender} is the sender's email used for scheduling.

  Your job is to extract the following fields:
  - Sender
  - Subject of the event
  - Start datetime (in ISO format)
  - End datetime (in ISO format)
  - Location of the event
  - Timezone of user
  - Attendees (comma-separated email addresses)

  Notes:
  - The current date and time is {current_date}.
  - Timezone: ALWAYS use {local_tz}
  - You MUST extract the weekday or date and time of the event.
  - You MUST recognize common misspellings like "tommorrow" as "tomorrow".
  - You MUST extract time ranges (e.g. "9 AM to 11 AM") and calculate both start and end.
  - You MUST call the NextWeekdayDateTool if a weekday is provided.
 
  - If only relative dates (like "tomorrow", "next week Monday", "coming Friday") are given, compute actual date directly.
  - Do not hardcode any example dates.
  - If no location is mentioned, use "Not specified".

    """,
    input_keys=["sender", "question", "current_date", "local_tz"],
    expected_output="A confirmation message with the created event details: subject, start (12-hour format with timezone), end (12-hour format with timezone), location, attendees.",
    agent=reminder_event_agent
)

categorizer_agent = Agent(
    role="Email Intent Categorizer",
    goal="Classify the email or user request into appropriate handling categories.",
    backstory="Trained to discern if an email needs a reply, contains a task, is a schedule request, or is informational.", 
    memory=False, verbose=False,
    llm_config={"model": "gpt-4", "temperature": 0.2},
    allow_delegation=False,
)

categorize_task = Task(
    description=(
    """
description: >
  You will receive two inputs:
  - {content}: the actual message body
  - {type}: the type of request, one of ["incoming_email", "user_request"]

  Your job is to classify the message into one of the following categories:

  - 'requires_response': Use this if:
    • The message asks the AGENT to do something (e.g., write/send/summarize)
    • OR the message is a casual greeting or general conversation ("Hi", "Hello there", "How are you?", etc.) and the type is 'user_request'

  - 'actionable_task': Use this if:
    • The type is 'incoming_email'
    • AND the message expects the USER (not the agent) to take action (e.g., "Can you attend...", "Please register", "You need to finalize...")

  - 'schedule_event': Use this if the message is about planning or confirming a meeting or calendar event.

  - 'reminder': Use this if the user is asking the agent to create a reminder or store a todo/calendar task (e.g., "Remind me to...", "Add a task to...")

  - 'no_action': Use this **only** if:
    • The type is 'incoming_email'
    • AND the content is FYI only, purely informational, or clearly not requiring any reply or action

  - 'spam': For promotional, irrelevant, or junk content.

IMPORTANT RULES:
- Do NOT classify as 'no_action' for user_request type — only incoming_email.
- If content says "Hi", "Hello", "How are you", etc. AND type is 'user_request' → classify as 'requires_response'.
- Only use 'actionable_task' for **incoming_email** when sender expects the human user to act.
- Only use 'requires_response' when agent is expected to act OR it's casual social user request.

Examples:
- "Hello there" + type=user_request → requires_response
- "How are you?" + type=user_request → requires_response
- "Hi" + type=user_request → requires_response
- "FYI we updated the database" + type=incoming_email → no_action
- "Please register for this event" + type=incoming_email → actionable_task
- "Send an email to John" + type=user_request → requires_response
- "Remind me to follow up tomorrow" + type=user_request → reminder
- "Buy now!" + type=incoming_email→ spam

Your final answer MUST be one of:
'requires_response', 'actionable_task', 'schedule_event', 'reminder', 'no_action', 'spam'
    """
    ),
    expected_output="One of: requires_response, actionable_task, schedule_event, reminder, no_action, spam",
    agent=categorizer_agent,
    input_keys=["content", "type"],
    output_keys=["category"]
)



# ────────────────────────────────────────────────────────────────────────────────
# 4) Summarizer Agent and Task (for “no_action” emails)
# ────────────────────────────────────────────────────────────────────────────────

summarizer_agent = Agent(
    role="Email Summarizer",
    goal="Generate a concise 2–3 sentence summary of a non-actionable email, then insert it into the database.",
    backstory=(
        "You receive an email’s subject, body, sender, timestamp, mail_id, and userId. "
        "First, produce a brief 2–3 sentence summary of that email’s main points. "
        "Then call the `insert_email_record` tool (which inserts a row into the Emails table) "
        "using exactly the structure: "
        "{\"summary\": <your_summary>, \"id\": <mail_id>, \"userId\": <userId>, \"subject\": <subject>, \"sender\": <sender>, \"body_preview\": <body_preview>}. "
        "Finally, output whatever confirmation string the tool returns."
    ),
    memory=False,
    verbose=False,
    tools=[insert_email_record],  # only this one tool is needed
    llm_config={"model": "gpt-o4-mini", "temperature": 0.2},
    allow_delegation=False,
)

# --- 2) Define a single Task that both summarises and then calls insert_email_record
summarize_and_insert_task = Task(
    description="""
You are given a non-actionable email with the following details:

Subject: {subject}
Sender: {sender}
Received DateTime: {receivedDateTime}
Body Preview: {bodyPreview}


Your task has 3 required steps:

1. Carefully summarize this email into 2–3 sentences.

2. Then call the tool `insert_email_record` using this exact structure:
{
  "summary": "<your summary>",
  "id": "{id}",
  "userId": {userId},
  "subject": "{subject}",
  "sender": "{sender}",
  "body_preview": "{bodyPreview}"
}

3. The tool will return a confirmation string. You MUST return that confirmation string and Summary as your output. Do NOT return anything else.
""",
    input_keys=["subject", "body", "sender", "receivedDateTime", "bodyPreview", "id", "userId"],
    expected_output="Confirmation string from insert_email_record tool",
    agent=summarizer_agent
)

#=============== Responder Router ================

intent_router_agent = Agent(
    role="Intent Router",
    goal="Classify user input into one of three categories: 'general question', 'ask about email ability', 'write email', or 'send email'.",
    backstory="You excel at understanding user intent. Your job is to classify whether the user is asking something general, asking about email capabilities, or wants to send an email.",
    memory=False,
    verbose=False,
    llm_config={"model": "gpt-4o-mini", "temperature": 0.2},
    allow_delegation=False,
)

intent_task = Task(
    description=(
        "Classify the message as one of:\n"
        "- 'general': for unrelated casual questions.\n"
        "- 'can you send email': if user is asking about capabilities.\n"
        "- 'write email': if user wants the system to draft an email.\n"
        "Message: {question}"
    ),
    expected_output="general|can you send email|write email|send email",
    agent=intent_router_agent,
)