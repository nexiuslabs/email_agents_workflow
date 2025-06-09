import logging
from crewai import Agent, Task, Crew, Process
from agents.email_agents import *
import json

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


email_reply_crew = Crew(
    agents=[draft_reply_agent],
    tasks=[generate_email_reply_task],
    process=Process.sequential,
    verbose=False
)

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
        summarizer_agent,
        human_action_agent,
        task_manager_agent
        ],
    tasks=[
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
        email_data_extractor_agent
       
        ],
    tasks=[
        extract_email_fields_task    
    ],
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
                "question": user_question,
                "sender": inputs.get("sender", ""),
                "conversation_id": inputs.get("conversation_id"),
                "attachments": inputs.get("attachments", None),
                "subject": "",
                "body":""
            }
        return Email_Crew_Pipeline(input_payload)

    else:
        raise ValueError("Manager Orchestrator received an unrecognized payload: neither 'id' nor 'question' present.")

def Email_Crew_Pipeline(input_payload):

    logger.info(f"[Orchestrator] Categorizing user command: {input_payload}")
    
    logger.info(f"[Orchestrator] Attachments: {input_payload.get('attachments', None)}")

 
    if input_payload['question'] and input_payload['question'].strip():
        content = input_payload['question']
    else:
        content = input_payload['bodyPreview']
    
    categorizer_payload = {
            "content": content,
        }
    
    logger.info(f"[Orchestrator] Categorizing user command: {content}")

    categorization_result = categorizer_crew.kickoff(inputs=categorizer_payload)
    category = str(getattr(categorization_result, "output", categorization_result)).strip().lower()
    logger.info(f"[Orchestrator] categorized as: {category}")

    if category == "requires_response":
        classification_result = intent_router_crew.kickoff(inputs=input_payload)
        intent = str(getattr(classification_result, "output", classification_result)).strip().lower()
        logger.info(f"[Orchestrator] Intent classified as: {intent}")

        # 2b) If intent == "general" or "can you send email" → delegate to casual_crew
        if intent in ["general", "can you send email"]:
            casual_result = casual_crew.kickoff(inputs=input_payload)
            answer = getattr(casual_result, "output", str(casual_result))
            return {"type": "casual_reply", "question": input_payload.get("question"), "answer": answer}

        elif intent == "write email":
            draft_payload = {
                "question": input_payload.get("question"),
                "sender_email": input_payload.get("sender")
            }
            email_result = email_reponder_crew.kickoff(inputs=draft_payload)
            answer = getattr(email_result, "output", str(email_result))
            return {"type": "email_written", "question": input_payload.get("question"), "answer": answer}

        # 2c) If intent == "send email" → use email_crew (draft, lookup, review, send)
        elif intent == "send email" :

            send_payload = {
                "question": input_payload.get("question"),
                "sender_email": input_payload.get("sender")
            }

            logger.info(f"[Orchestrator] Sending email: {send_payload}")
            send_result = email_onboard_crew.kickoff(inputs=send_payload)
            raw_output = str(send_result)

            # Strip triple backticks and markdown labeling if present
            clean_output = raw_output.strip().removeprefix("```json").removesuffix("```").strip()

            # Now parse clean JSON
            final_json = json.loads(clean_output)
            
            sender = final_json.get("sender")
            receiver = final_json.get("receiver")
            subject = final_json.get("subject")
            content = final_json.get("content")
            attachments = final_json.get("attachments")

            result = send_email.run(
                sender=sender,
                receiver=receiver,
                subject=subject,
                content=content,
                attachments= attachments if attachments else None
            )
            # email_crew’s final “output” is usually the send_result (confirmation string or error)
            answer = getattr(result, "output", str(result))
            return {"type": "email_sent", "question": input_payload.get("question"), "answer": answer}

        else:
            # (Fallback: anything else we didn’t explicitly recognize → treat as “general”)
            fallback_result = casual_crew.kickoff(inputs=input_payload)
            answer = getattr(fallback_result, "output", str(fallback_result))
            return {"type": "casual_reply", "question": input_payload.get("question"), "answer": answer}

    elif category == "actionable_task":
        create_task_payload = {
            "id": input_payload.get("id"), 
            "receivedDateTime": input_payload.get("receivedDateTime"), 
            "subject": input_payload.get("subject"), 
            "bodyPreview": input_payload.get("bodyPreview"), 
            "sender": input_payload.get("sender"), 
            "receiver": input_payload.get("receiver"),
            "userId": input_payload.get("userId")
        }

        task_execution_result = email_task_execution_crew.kickoff(inputs=create_task_payload)
        answer = getattr(task_execution_result, "output", str(task_execution_result))
        return {"type": "actionable_task", "question": input_payload.get("question"),"answer": answer}

    elif category == "spam/irrelevant":
        return {"type": "spam/irrelevant", "question": input_payload.get("question"),"answer": "The message was skipped. It was not relevant to the user."}

    elif category == "no_action":

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
        return {"type": "no_action", "question": input_payload.get("question"),"answer": answer}

    else:
        return {"type": "unknown_category", "question": input_payload.get("question"),"answer": "The message was skipped. Unknown Category."}