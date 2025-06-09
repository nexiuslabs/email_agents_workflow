from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.exception_handlers import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from db_utils import record_qa, update_conversation_title, upload_file
from crew import manager_orchestrator
import base64
import json
from jwt_auth import verify_jwt_token
from dotenv import load_dotenv
from logging import basicConfig, getLogger
import logging

load_dotenv()

basicConfig(level=logging.INFO)
logger = getLogger(__name__)

# Initialize FastAPI without global auth dependency
app = FastAPI()

# Log validation errors for easier debugging
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"422 Validation error for {request.url}: {exc.errors()} | Body: {await request.body()}")
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

# Other legacy endpoints remain untouched or can be updated similarly.
