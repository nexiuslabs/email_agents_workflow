import logging
from crewai import Agent, Task, Crew, Process
from agents.email_agents import *
import json
import datetime
from fastapi import Body
from fastapi.responses import JSONResponse
from fastapi import HTTPException
from fastapi import Request
from db_utils import insert_record
current_utc_date = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
from tzlocal import get_localzone_name

local_tz = get_localzone_name() 
# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


email_attachment_crew = Crew(
    agents=[
        email_drafting_agent,
        receiver_email_lookup_agent,
        email_review_agent,
        email_data_extractor_agent,
        
    ],
    tasks=[
        process_email_task,
        lookup_receiver_email_task,
        review_email_task,
        extract_email_fields_task
    ],
    manager_agent=intent_router_agent,
    process=Process.sequential,
    verbose=True,
)


#================= Unified Crews =================

categorizer_crew = Crew(
    agents=[
        categorizer_agent,
        ],
    tasks=[
        categorize_task
    ],
    process=Process.sequential,
    verbose=True
)


intent_router_crew = Crew(
    agents=[intent_router_agent],
    tasks=[intent_task],
    process=Process.sequential,
    verbose=True
)

email_reponder_crew = Crew(
    agents=[
        email_drafting_agent,
        receiver_email_lookup_agent,
        email_review_agent,
        email_data_extractor_agent,
        email_format_agent,
    
        ],
    tasks=[
        process_email_task,
        lookup_receiver_email_task,
        review_email_task,
        extract_email_fields_task,
        email_format_task,
        
    ],
    process=Process.sequential,
    verbose=True
)

email_task_execution_crew = Crew(
    agents=[       
        auto_draft_reply_agent, 
        summarizer_agent,
        human_action_agent,
        task_manager_agent
        ],
    tasks=[
        auto_email_draft_reply_task,
        summarize_and_insert_task,
        analyze_email_task,
        create_task_records_task
        ],
    process=Process.sequential,
    verbose=True
)

no_action_crew = Crew(
    agents=[summarizer_agent],
    tasks=[summarize_and_insert_task],
    process=Process.sequential,
    verbose=True
)

casual_crew = Crew(
    agents=[email_support_conversation_agent],
    tasks=[email_conversation_task],
    process=Process.sequential,
    verbose=False
)


email_onboard_crew = Crew(
    agents=[
        email_sender_data_extractor_agent
       
        ],
    tasks=[
        extract_email_sender_fields_task    
    ],
    process=Process.sequential,
    verbose=True
)


email_sender_crew = Crew(
    agents=[
        email_sender_data_extractor_agent
       
        ],
    tasks=[
        extract_email_sender_fields_task    
    ],
    process=Process.sequential,
    verbose=True
)


email_draft_reply_crew = Crew(
    agents=[
        draft_reply_agent
        ],
    tasks=[
        generate_email_reply_task    
    ],
    process=Process.sequential,
    verbose=True
)

#_____________________________________________
#           Reminder Crews
#_____________________________________________

reminder_todo_crew = Crew(
    agents=[reminder_todo_formatter_preview_agent, reminder_todo_agent],
    tasks=[reminder_todo_formatter_preview_task, reminder_todo_task],
    process=Process.sequential
)
reminder_event_crew = Crew(
    agents=[event_formatter_preview_agent, reminder_event_agent ],
    tasks=[event_formatter_preview_task, reminder_event_task],
    process=Process.sequential,
    verbose=True
)

reminder_crew = Crew(
    agents=[reminder_agent],
    tasks=[reminder_task],
    process=Process.sequential,
    verbose=True
)


# ─── New: Manager Orchestrator ───────────────────────────────────────────────────
def manager_orchestrator(inputs: dict):
    """
    A single entry point to handle either:
      • an incoming‐email webhook (contains “id”, “subject”, “bodyPreview”, etc.)
      • or a user “send email” / “ask question” chat request (contains “question”, “sender_email”, “conversation_id”).
    This function inspects its keys and dispatches to the appropriate sub‐crew.

    - If 'id' is in inputs: treat as an incoming email event → run `email_task_pipeline`.
    - If 'question' is in inputs:  run the intent classifier, then either `casual_crew` or `email_crew`.
    """

    # ─── 1) Incoming‐Email Event ─────────────────────────────────────────────────
    if "id" in inputs:
        # The payload from the webhook might look like:
        # {
        #   "id": "22331kmxkwl1",
        #   "subject": "Invitation for AI Training",
        #   "bodyPreview": "Hello Dear Henry, Please join the AI Training on Monday 9 AM",
        #   "sender": "darryl@nexiuslabs.com",
        #   "receiver": "henry@nexiuslabs.com"
        #   // (optionally) "userId": 123  # the internal user ID if you have it
        # }
        #
        # We only need to pass exactly what `email_task_pipeline` expects:
        #   • The “summary” field can be the bodyPreview (even though it’s called summary here, filter_and_route uses it as preview text)
        #   • id → mail ID
        #   • userId → internal user’s ID
        #
        # If you need the full email body instead of just the preview, you could first call `read_email_by_task_id`
        # to fetch the full content; for now we’ll just forward “bodyPreview”.

        kickoff_payload = {

            "type": "incoming_email",
            "bodyPreview": inputs.get("bodyPreview", ""),
            "subject": inputs.get("subject", ""),
            "sender": inputs.get("sender", ""),
            "receiver": inputs.get("receiver", ""),
            "receivedDateTime": inputs.get("receivedDateTime", ""),
            "id": inputs["id"],
            "userId": inputs.get("userId"),  # Optional: if you store a numeric userId
            "body": inputs.get("body", ""),
            "question": inputs.get("question", ""),
            "conversation_id": inputs.get("conversation_id", "")
        }

        logger.info(f"[Orchestrator] Incoming email detected. Running email_task_pipeline with: {kickoff_payload}")
        incoming_email_result = Email_Crew_Pipeline(kickoff_payload)
        return {"status": "incoming_processed", "result": getattr(incoming_email_result, "output", str(incoming_email_result))}

    # ─── 2) User “Ask” / “Send Email” Command ─────────────────────────────────────
    elif "question" in inputs:
        # Example inputs for user request:
        # {
        #   "question": "Write email to henry@nexiuslabs.com about Town Hall…",
        #   "sender_email": "melverick@nexiuslabs.com",
        #   "conversation_id": 425
        # }
        user_question = inputs["question"].strip()
        logger.info(f"[Orchestrator] User command detected: \"{user_question}\"")
        input_payload = {
            "type": "user_request",
            "question": user_question,
            "sender": inputs.get("sender", ""),
            "conversation_id": inputs.get("conversation_id"),
            "attachments": inputs.get("attachments", None),
            "current_date": current_utc_date,
            "subject": "",
            "body":""
        }
        return Email_Crew_Pipeline(input_payload)

    else:
        raise ValueError("Manager Orchestrator received an unrecognized payload: neither 'id' nor 'question' present.")

def Email_Crew_Pipeline(input_payload):

    logger.info(f"[Orchestrator] Categorizing user command: {input_payload}")
    
    logger.info(f"[Orchestrator] Attachments: {input_payload.get('attachments', None)}")

 
    if input_payload.get("question", "").strip():
        content = input_payload["question"]
    elif input_payload.get("body", "").strip():
        content = input_payload["body"]
    else:
        content = input_payload.get("body", "")
    
    categorizer_payload = {
            "content": content,
            "type": input_payload.get("type")
        }
    
    logger.info(f"[Orchestrator] Categorizing user command: {content}")

    categorization_result = categorizer_crew.kickoff(inputs=categorizer_payload)
    category = str(getattr(categorization_result, "output", categorization_result)).strip().lower()
    logger.info(f"[Orchestrator] categorized as: {category}")
    logger.info(f"[Orchestrator] type: {input_payload.get('type')}")

    conversation_id = input_payload.get("conversation_id")

    if category == "requires_response" and input_payload.get("type") == "user_request":
        classification_result = intent_router_crew.kickoff(inputs=input_payload)
        intent = str(getattr(classification_result, "output", classification_result)).strip().lower()
        logger.info(f"[Orchestrator] Intent classified as: {intent}")

        # 2b) If intent == "general" or "can you send email" → delegate to casual_crew
        if intent in ["general", "can you send email"]:
            casual_result = casual_crew.kickoff(inputs=input_payload)
            answer = getattr(casual_result, "output", str(casual_result))
            insert_record(conversation_id, input_payload.get("question"), answer)
            return {"type": "casual_reply", "question": input_payload.get("question"), "answer": answer}

        elif intent == "write email":
            draft_payload = {
                "question": input_payload.get("question"),
                "sender_email": input_payload.get("sender")
            }
            email_result = email_reponder_crew.kickoff(inputs=draft_payload)
            answer = getattr(email_result, "output", str(email_result))
            insert_record(conversation_id, input_payload.get("question"), answer)
            return {"type": "email_written", "question": input_payload.get("question"), "answer": answer}


        # 2c) If intent == "send email" → use email_crew (draft, lookup, review, send)
        # elif intent == "send email" :

        #     send_payload = {
        #         "question": input_payload.get("question"),
        #         "sender_email": input_payload.get("sender")
        #     }

        #     logger.info(f"[Orchestrator] Sending email: {send_payload}")
        #     send_result = email_onboard_crew.kickoff(inputs=send_payload)
        #     raw_output = str(send_result)

        #     # Strip triple backticks and markdown labeling if present
        #     clean_output = raw_output.strip().removeprefix("

        else:
            # (Fallback: anything else we didn’t explicitly recognize → treat as “general”)
            fallback_result = casual_crew.kickoff(inputs=input_payload)
            answer = getattr(fallback_result, "output", str(fallback_result))
            insert_record(conversation_id, input_payload.get("question"), answer)
            return {"type": "casual_reply", "question": input_payload.get("question"), "answer": answer}

    elif category == "actionable_task" and input_payload.get("type") == "incoming_email":
        create_task_payload = {
            "id": input_payload.get("id"), 
            "receivedDateTime": input_payload.get("receivedDateTime"), 
            "subject": input_payload.get("subject"), 
            "bodyPreview": input_payload.get("bodyPreview"), 
            "sender": input_payload.get("sender"), 
            "receiver": input_payload.get("receiver"),
            "userId": input_payload.get("userId"),
            # Payload for draft reply
            "mail_id": input_payload.get("id"),
            "sender_email": input_payload.get("sender"),

        }

        task_execution_result = email_task_execution_crew.kickoff(inputs=create_task_payload)

        answer = getattr(task_execution_result, "output", str(task_execution_result))
        return {"type": "actionable_task","answer": answer}

    elif category == "spam/irrelevant" and input_payload.get("type") == "incoming_email":
        return {"type": "spam/irrelevant","answer": "The message was skipped. It was not relevant to the user."}

    elif category == "reminder" and input_payload.get("type") == "user_request":
        reminder_payload = {
            "question": input_payload.get("question"),
            "sender": input_payload.get("sender"),
        }
        reminder_result = reminder_crew.kickoff(inputs=reminder_payload)
        reminder_type = getattr(reminder_result, "output", str(reminder_result))
        if reminder_type == "todo":
            try:
                # Prepare the payload for the crew
                todo_payload = {
                    "sender": input_payload.get("sender"),
                    "question": input_payload.get("question"),
                    "current_date": current_utc_date,
                }
                result = reminder_todo_crew.kickoff(inputs=todo_payload)
                answer = getattr(result, "output", str(result))
                insert_record(conversation_id, input_payload.get("question"), answer)
                return JSONResponse(content={
                    "type": "reminder_created",
                    "question": input_payload.get("question"),
                    "answer": answer
                })
            except Exception as e:
                logger.exception("Error in /todoTask endpoint:")
                raise HTTPException(status_code=500, detail=str(e))

        elif reminder_type == "event":
            try:
                event_payload = {
                    "sender": input_payload.get("sender"),
                    "question": input_payload.get("question"),
                    "current_date": current_utc_date,
                    "local_tz": local_tz
                }
                result = reminder_event_crew.kickoff(inputs=event_payload)
                answer = getattr(result, "output", str(result))
                insert_record(conversation_id, input_payload.get("question"), answer)
                return JSONResponse(content={
                    "type": "event_created",
                    "question": input_payload.get("question"),
                    "answer": answer
                })
            except Exception as e:
                logger.exception("Error in /event endpoint:")
                raise HTTPException(status_code=500, detail=str(e))

    elif category == "schedule_event" and input_payload.get("type") == "user_request":
        try:
            event_payload = {
                "sender": input_payload.get("sender"),
                "question": input_payload.get("question"),
                "current_date": current_utc_date,
                "local_tz": local_tz
            }
            result = reminder_event_crew.kickoff(inputs=event_payload)
            answer = getattr(result, "output", str(result))
            insert_record(conversation_id, input_payload.get("question"), answer)
            return JSONResponse(content={
                "type": "event_created",
                "question": input_payload.get("question"),
                "answer": answer
            })
        except Exception as e:
            logger.exception("Error in /event endpoint:")
            raise HTTPException(status_code=500, detail=str(e))
        
    elif category == "no_action" and input_payload.get("type") == "incoming_email":

        summary_payload = {

            "id": input_payload["id"],
            "receivedDateTime": input_payload["receivedDateTime"],  # must match 'timestamp'
            "subject": input_payload["subject"],
            "bodyPreview": input_payload["bodyPreview"],            # must match 'body'
            "sender": input_payload["sender"],
            "userId": input_payload["userId"]
        }

        summary_execution_result = no_action_crew.kickoff(inputs=summary_payload)
        answer = getattr(summary_execution_result, "output", str(summary_execution_result))
        return {"type": "no_action","answer": answer}

    else:
        return {"type": "unknown_category","answer": "The message was skipped. Unknown Category."}