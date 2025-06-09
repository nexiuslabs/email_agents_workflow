import logging
from crewai import Agent, Task, Crew, Process
from tools.create_tasks_tool import create_tasks_from_summary
from tools.natural_language_date_parser import NormalizeDueDatesTool
from tools.send_email_tool import send_email
from tools.get_receiver_email_tool import get_contact_email_by_name
from tools.read_email_tool import read_email_by_task_id
from tools.insert_email_tool import insert_email_record

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

#=============== Email Drafting ================

email_drafting_agent = Agent(
    role="Email Drafter and Formatter",
    goal="Create a properly structured professional email from user instruction.",
    backstory="You're a communication specialist for Nexius Labs with experience writing professional emails from user-provided ideas.",
    memory=True,
    verbose=True,
    llm_config={"model": "gpt-o4-mini", "temperature": 0.3},
)

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


intent_task = Task(
    description=(
        "Classify the message as one of:\n"
        "- 'general': for unrelated casual questions.\n"
        "- 'can you send email': if user is asking about capabilities.\n"
        "- 'write email': if user wants the system to draft an email.\n"
        "- 'send email': if user wants the system to draft and send email now.\n\n"
        "Message: {question}"
    ),
    expected_output="general|can you send email|write email|send email",
    agent=intent_router_agent,
)

process_email_task = Task(
    description="""
From the following user instruction, do the following:
- Extract the recipient's name or email.
- Infer an appropriate subject line from the context.
- Write a professional business email body using Nexius Labs' tone.
- Ensure the message ends with a signature that includes the sender's name (parsed from sender_email) and the company name 'Nexius Labs'.

Signature format example:
Best regards,  
John Doe  
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

send_email_task = Task(
    description="Send the finalized email via Microsoft Graph API. Include attachments from the context if any.",
    expected_output="Confirmation message with email details or error.",
    input_keys=["receiver", "sender", "subject", "content"],
    context_keys=["attachments"],  # ✅ This is the missing link
    agent=email_sender_agent,
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
    backstory="You are a language-savvy assistant with exceptional written communication skills."
    "Your purpose is to streamline email communication by drafting high-quality responses"
    "that maintain proper tone, structure, and clarity.",
    memory=False,
    verbose=True,
    tools=[read_email_by_task_id],
    llm_config={"model": "gpt-o4-mini", "temperature": 0.2},
    allow_delegation=False,
)

generate_email_reply_task = Task(
    description="""
Use the `read_email_by_task_id` tool to retrieve email content.
Pass in a dictionary like: {"task_id": {task_id}, "sender_email": {sender_email}}.

Then, generate a professional reply email that:
- Starts with a greeting
- Ends with:
Best regards,
Henry
""",
    input_keys=["task_id", "sender_email"],
    expected_output="Plain text reply email.",
    agent=draft_reply_agent
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
# 3) Categorizer Agent and Task
# ────────────────────────────────────────────────────────────────────────────────

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
  You will receive a single string called {content}.

  Your job is to classify this {content} into one of the following categories:

  - 'requires_response': expects a direct reply, asks for information, OR requests the AGENT to perform an action on behalf of the user (such as writing an email, sending information, or executing an instruction), OR is a greeting/conversational opener where a social reply is typically expected.

  - 'actionable_task': contains statements where the USER themselves intends to perform an action or is confirming/planning an action they will do (not asking the agent to do it, nor explicitly asking for a reply). This includes language such as "I will", "I plan to", "We will", "I would like to prepare", "I would like to discuss", "We are confirming", "I will ensure", etc.

  - 'schedule_event': is about scheduling meetings or events.

  - 'no_action': purely informational, trivial, FYI only, no response or tasks needed.

  - 'spam': spam, marketing, or irrelevant content.

  IMPORTANT RULE:
  - If the content contains both instructions for the agent and other actions or information, you should prioritize classifying the message as 'requires_response'.
  - For example: If the content says "Write an email and prepare a report", since the first instruction is asking the AGENT to perform an action, the correct classification is 'requires_response'.

  IMPORTANT GUIDELINES:
  - Do NOT classify as 'requires_response' just because the text ends with 'please let me know' — consider the main intent.
  - Treat greetings ("Hi", "Hello there", "Good morning", etc.) as 'requires_response'.
  - If the content describes scheduling, even if polite, classify as 'schedule_event'.
  - If the content explicitly asks the AGENT to perform an action (write email, send email, generate, notify, summarize, etc.), classify as 'requires_response' even if the content also contains other task words like 'prepare' or 'finalize'.
  - If the content contains tasks or requests clearly intended for the USER to perform action step, classify as 'actionable_task'.
  - If the content is clearly junk or ads, classify as 'spam'.
  - Use your best judgment to pick exactly ONE category.

  Examples:
  - "Hello there" -> requires_response
  - "Hi" -> requires_response
  - "Good afternoon" -> requires_response
  - "Can you provide the report?" -> requires_response
  - "Please write an email to John about the project." -> requires_response
  - "Send Email to <receiver>" -> requires_response
  - "Send the project summary to the team." -> requires_response
  - "Generate the monthly report and email it to finance." -> requires_response
  - "Write email to henry about the event and prepare the event agenda." -> requires_response
  - "FYI, we updated the server." -> no_action
  - "Please schedule a meeting for next week." -> schedule_event
  - "Prepare the budget by Friday." -> actionable_task
  - "You need to finalize the presentation slides." -> actionable_task
  - "I would like to prepare the event materials." -> actionable_task
  - "send email to henry@nexiuslabs.com,
    Subject : Preparation for Product Announcement Event on Tuesday,
    Dear Henry,
    I hope this message finds you well. I am writing to confirm your participation in the upcoming Product Announcement Event at MICT Park next Tuesday.
    Please ensure that all technical documentation is prepared and finalized before the end of this week, specifically by Friday, to ensure a smooth presentation during the event.
    If you have any questions or require assistance, feel free to reach out.Best regards,  
    Henry  
    Nexius Labs" -> requires_response

  - "We are confirming our participation in the event." -> actionable_task
  - "I plan to finalize the agenda this week." -> actionable_task
  - "I will coordinate with the team on the logistics." -> actionable_task
  - "Please send the finalized agenda." -> requires_response
  - "Can you confirm the participant list?" -> requires_response
  - "Please write an email to John about the project." -> requires_response
  - "Buy now!!!" -> spam

    """
    ),
    expected_output="One of: requires_response, actionable_task, schedule_event, no_action, spam, send_email, ask_capability, general",
    agent=categorizer_agent,
    input_keys=["content"],
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
        "{\"summary\": <your_summary>, \"id\": <mail_id>, \"userId\": <userId>, \"subject\": <subject>, \"sender\": <sender>}. "
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
  "sender": "{sender}"
}

3. The tool will return a confirmation string. You MUST return that confirmation string and Summary as your output. Do NOT return anything else.
""",
    input_keys=["subject", "body", "sender", "receivedDateTime", "bodyPreview", "id", "userId"],
    expected_output="Confirmation string from insert_email_record tool",
    agent=summarizer_agent
)

