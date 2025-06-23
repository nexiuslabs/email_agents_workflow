from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.exception_handlers import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from db_utils import insert_record, update_conversation_title, upload_file, update_draft_reply
from crew import manager_orchestrator
import base64
import json
from jwt_auth import verify_jwt_token
from dotenv import load_dotenv
from logging import basicConfig, getLogger
import logging
from fastapi import Body
from fastapi.responses import JSONResponse
import datetime
from tools.reply_email_tool import reply_to_latest_email
from tzlocal import get_localzone_name

local_tz = get_localzone_name() 

current_utc_date = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

load_dotenv()

basicConfig(level=logging.INFO)
logger = getLogger(__name__)

# Initialize FastAPI without global auth dependency
app = FastAPI()

# Log validation errors for easier debugging
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"422 Validation error for {request.url}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": exc.body},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request/response models
class QuestionRequest(BaseModel):
    type: str
    question: str
    sender: str
    conversation_id: int

class AnswerResponse(BaseModel):
    type: str
    question: str
    answer: str

class IncomingEmailEvent(BaseModel):
    """
    Model for an incoming‐email webhook. The JSON sent here must include:
      • id          (the Graph mail ID)
      • subject
      • bodyPreview
      • sender      (sender’s email address)
      • receiver    (recipient’s email address = our user’s mailbox)
      • (optional) userId  – internal user ID, if we know it
    """
    type: str
    id: str
    subject: str
    bodyPreview: str
    body: str
    sender: str
    receiver: str
    userId: Optional[int] = None
    receivedDateTime: Optional[str] = None

from fastapi import Form, File, UploadFile  # <— make sure these are imported
import base64

# …
@app.post("/ask", dependencies=[Depends(verify_jwt_token)], response_model=AnswerResponse)
async def ask_question(
    type: str = Form(...),
    conversation_id: str = Form(...),
    sender: str = Form(...),
    question: str = Form(...),
    files: List[UploadFile] = File(default=None),
):
    """
    Accepts:
      • type             – what kind of request this is (e.g. "send_email", "ask_question")
      • conversation_id  – your internal thread ID
      • sender           – the user’s email or identifier
      • question         – the free-text prompt
      • files            – zero or more file attachments
    """
    try:
        # 1) Read & encode attachments, if any
        attachments = []
        if files:
            for f in files:
                data = await f.read()
                attachments.append({
                    "filename": f.filename,
                    "size": len(data),
                    "content": base64.b64encode(data).decode("utf-8"),
                    "content_type": f.content_type or "application/octet-stream",
                })

        # 2) Build the payload for your orchestrator
        orchestrator_input = {
            "type": type,
            "conversation_id": conversation_id,
            "sender": sender,
            "question": question,
            "attachments": attachments or None,
        }

        # 3) Dispatch to crew.py
        result = manager_orchestrator(orchestrator_input)
        return result

    except Exception as e:
        logger.exception("Error in /ask endpoint with form data:")
        raise HTTPException(status_code=500, detail=str(e))

# Incoming email webhook: no auth required
@app.post("/incoming_email")
def incoming_email(event: IncomingEmailEvent):
    """
    Receives a webhook POST whenever a new email arrives. The body must match IncomingEmailEvent.
    We forward the payload into manager_orchestrator, which will run email_task_pipeline and return
    whatever the pipeline returned (either “dropped” or a list of tasks).
    """
    try:
        orchestrator_input = event.dict()
        result = manager_orchestrator(orchestrator_input)
        return {"status": "ok", "detail": result}

    except Exception as e:
        logging.exception("Error in /incoming_email endpoint:")
        raise HTTPException(status_code=500, detail=str(e))

# File upload endpoint: protected
@app.post("/upload", dependencies=[Depends(verify_jwt_token)])
def upload_file_endpoint(file: UploadFile = File(...)):
    try:
        contents = file.file.read()
        b64data = base64.b64encode(contents).decode("utf-8")
        upload_file(file.filename, b64data)  # reuse upload logic
        return {"filename": file.filename}
    except Exception as e:
        logging.exception("Error uploading file:")
        raise HTTPException(status_code=500, detail=str(e))

# Draft reply endpoint: protected
from crew import email_draft_reply_crew
from pydantic import BaseModel

class DraftReplyRequest(BaseModel):

    sender: str
    mail_id: str
    question: str
    attachments: List[str] = []

@app.post("/replyMail", dependencies=[Depends(verify_jwt_token)])
async def draft_reply_endpoint(
    question: str = Form(...),
    sender: str = Form(...),
    files: List[UploadFile] = File(default=None),
    mail_id: str = Form(...)):
    """
    Endpoint to send an email using the orchestrated pipeline.
    Accepts: {question, sender, attachments}
    """
    try:
        # 1) Read & encode attachments, if any
        attachments = []
        if files:
            for f in files:
                data = await f.read()
                attachments.append({
                    "filename": f.filename,
                    "size": len(data),
                    "content": base64.b64encode(data).decode("utf-8"),
                    "content_type": f.content_type or "application/octet-stream",
                })

        # 2) Build the payload for your orchestrator
        send_payload = {
            "question": question,
            "sender_email": sender,
            "attachments": attachments or None,
            "mail_id": mail_id
        }

        result = reply_to_latest_email.run(
            sender_email=sender,
            comment=question,
            attachment=attachments if attachments else None,
            mail_id=mail_id
        )
        if result:
            answer = f"Email was sent to {sender} successfully"
        else:
            answer = "Sorry, there is problem occured. Failed to Sent Email"
        return JSONResponse(content={"type": "email_sent", "question": question, "answer": answer})
    except Exception as e:
        logger.exception("Error in /draftReply endpoint:")
        raise HTTPException(status_code=500, detail=str(e))

# Preview draft reply endpoint: protected
class PreviewDraftReplyRequest(BaseModel):
    sender: str

    mail_id: str

@app.post("/draftReplyPreview", dependencies=[Depends(verify_jwt_token)])
def preview_draft_reply_endpoint(
    mail_id: str = Form(...),
    sender: str = Form(...)):
    """
    Endpoint to preview a draft reply using the email_draft_reply_crew.
    Accepts: {task_id, sender}
    """
    try:
        payload = {
            "mail_id": mail_id,
            "sender_email": sender
        }
        result = email_draft_reply_crew.kickoff(inputs=payload)
        answer = getattr(result, "output", str(result))
        return JSONResponse(content={
            "type": "draft_preview",
            "answer": answer
        })
    except Exception as e:
        logger.exception("Error in /draftReplyPreview endpoint:")
        raise HTTPException(status_code=500, detail=str(e))

class SendEmailRequest(BaseModel):
    question: str
    sender: str
    attachments: Optional[list] = None

@app.post("/sendEmail", dependencies=[Depends(verify_jwt_token)])
async def send_email_endpoint(
    question: str = Form(...),
    sender: str = Form(...),
    files: List[UploadFile] = File(default=None),
):
    """
    Endpoint to send an email using the orchestrated pipeline.
    Accepts: {question, sender, attachments}
    """
    try:
        # 1) Read & encode attachments, if any
        attachments = []
        if files:
            for f in files:
                data = await f.read()
                attachments.append({
                    "filename": f.filename,
                    "size": len(data),
                    "content": base64.b64encode(data).decode("utf-8"),
                    "content_type": f.content_type or "application/octet-stream",
                })

        # 2) Build the payload for your orchestrator
        send_payload = {
            "question": question,
            "sender_email": sender,
            "attachments": attachments or None,
        }

        logger.info(f"[Orchestrator] Sending email: {send_payload}")
        send_result = email_onboard_crew.kickoff(inputs=send_payload)
        raw_output = str(send_result)

        # Strip triple backticks and markdown labeling if present
        clean_output = raw_output.strip().removeprefix('"json').removesuffix('"""').strip()

        # Now parse clean JSON
        final_json = json.loads(clean_output)
        sender = sender
        receiver = final_json.get("receiver")
        subject = final_json.get("subject")
        content = final_json.get("content")
        attachments = attachments

        result = send_email.run(
            sender=sender,
            receiver=receiver,
            subject=subject,
            content=question,
            attachments=attachments if attachments else None
        )
        if result:
            answer = f"Email was sent to {receiver} successfully"
        else:
            answer = "Sorry, there is problem occured. Failed to Sent Email"
        return JSONResponse(content={"type": "email_sent", "question": question, "answer": answer})
    except Exception as e:
        logger.exception("Error in /sendEmail endpoint:")
        raise HTTPException(status_code=500, detail=str(e))

# ──────────────────────────────────────────────────────────────
# Reminder Endpoint (from crew.py logic)
# ──────────────────────────────────────────────────────────────

from crew import email_onboard_crew, reminder_crew, reminder_todo_crew
from crew import send_email


class ReminderRequest(BaseModel):
    sender: str
    question: str

@app.post("/todoTask", dependencies=[Depends(verify_jwt_token)])
def todo_task_endpoint(request: ReminderRequest = Body(...)):
    """
    Endpoint to create a Microsoft To Do reminder task for a user.
    Accepts: {email, task_title, task_body (optional), due_date_time (optional)}
    """
    try:
        input_payload = request.dict()
        # Prepare the payload for the crew
        reminder_payload = {
            "sender": input_payload.get("sender"),
            "question": input_payload.get("question"),
            "current_date": current_utc_date,
        }
        result = reminder_todo_crew.kickoff(inputs=reminder_payload)
        answer = getattr(result, "output", str(result))
        return JSONResponse(content={
            "type": "reminder_created",
            "question": input_payload.get("question"),
            "answer": answer
        })
    except Exception as e:
        logger.exception("Error in /todoTask endpoint:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reminder", dependencies=[Depends(verify_jwt_token)])
def reminder_endpoint(request: ReminderRequest = Body(...)):
    """
    Endpoint to create a Microsoft To Do reminder task for a user.
    Accepts: {email, task_title, task_body (optional), due_date_time (optional)}
    """
    try:
        input_payload = request.dict()
        # Prepare the payload for the crew
        reminder_payload = {
            "sender": input_payload.get("sender"),
            "question": input_payload.get("question"),
        }
        result = reminder_crew.kickoff(inputs=reminder_payload)
        answer = getattr(result, "output", str(result))
        return JSONResponse(content={
            "type": "reminder_created",
            "question": input_payload.get("question"),
            "answer": answer
        })
    except Exception as e:
        logger.exception("Error in /reminder endpoint:")
        raise HTTPException(status_code=500, detail=str(e))


class EventRequest(BaseModel):
    sender: str
    question: str

@app.post("/event", dependencies=[Depends(verify_jwt_token)])
def event_endpoint(request: EventRequest = Body(...)):
    """
    Endpoint to create a calendar event or event reminder for a user.
    Accepts: {sender, question}
    """
    try:
        input_payload = request.dict()
        event_payload = {
            "sender": input_payload.get("sender"),
            "question": input_payload.get("question"),
            "current_date": current_utc_date,
            "local_tz": local_tz
        }
        from crew import reminder_event_crew
        result = reminder_event_crew.kickoff(inputs=event_payload)
        answer = getattr(result, "output", str(result))
        return JSONResponse(content={
            "type": "event_created",
            "question": input_payload.get("question"),
            "answer": answer
        })
    except Exception as e:
        logger.exception("Error in /event endpoint:")
        raise HTTPException(status_code=500, detail=str(e))




