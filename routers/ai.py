from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import os
import json
import re

from database import get_db
import models
import schemas
from auth import get_current_user

router = APIRouter(prefix="/ai", tags=["AI (Gemini)"])


# ---------------------------------------------------------------------------
# Gemini client helper
# ---------------------------------------------------------------------------

def _get_gemini_model():
    """Return a configured Gemini GenerativeModel instance."""
    try:
        import google.generativeai as genai
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="google-generativeai package not installed",
        )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GEMINI_API_KEY not configured",
        )

    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-flash")


def _call_gemini(prompt: str) -> str:
    """Call Gemini and return the text response."""
    model = _get_gemini_model()
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini API error: {str(exc)}",
        )


def _extract_json(text: str):
    """Try to parse JSON from Gemini response, stripping markdown fences."""
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return text


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/generate-subtasks", response_model=schemas.AIResponse)
def generate_subtasks(
    req: schemas.AIGenerateSubtasksRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Break a task into actionable subtasks using Gemini."""
    prompt = (
        f"You are a project management assistant. Break the following task into clear, "
        f"actionable subtasks (maximum 8).\n\n"
        f"Task Title: {req.task_title}\n"
        f"Description: {req.task_description or 'No description provided'}\n\n"
        f"Return ONLY a JSON array of strings, each being a subtask title. "
        f'Example: ["Research competitors", "Draft outline", "Write first draft"]'
    )
    raw = _call_gemini(prompt)
    data = _extract_json(raw)

    if not isinstance(data, list):
        data = [raw]

    return schemas.AIResponse(success=True, data=data, message=f"Generated {len(data)} subtasks")


@router.post("/suggest-priority", response_model=schemas.AIResponse)
def suggest_priority(
    req: schemas.AISuggestPriorityRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Suggest priority and deadline for a task using Gemini."""
    prompt = (
        f"You are a project management assistant. Analyze the following task and suggest "
        f"an appropriate priority level and deadline.\n\n"
        f"Task Title: {req.task_title}\n"
        f"Description: {req.task_description or 'No description provided'}\n"
        f"Deadline Hint: {req.deadline_hint or 'None provided'}\n\n"
        f"Return ONLY valid JSON with this structure:\n"
        f'{{"priority": "high|medium|low", "suggested_deadline_days": <integer>, '
        f'"reasoning": "<brief explanation>"}}'
    )
    raw = _call_gemini(prompt)
    data = _extract_json(raw)
    return schemas.AIResponse(success=True, data=data, message="Priority suggestion generated")


@router.post("/daily-summary", response_model=schemas.AIResponse)
def daily_summary(
    req: schemas.AIDailySummaryRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate a daily summary of tasks for the authenticated user (or all tasks for admin)."""
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    user_id = req.user_id or current_user.id

    completed = (
        db.query(models.Task)
        .filter(
            models.Task.assigned_to == user_id,
            models.Task.status == models.TaskStatus.completed,
            models.Task.updated_at >= today_start,
        )
        .all()
    )

    in_progress = (
        db.query(models.Task)
        .filter(
            models.Task.assigned_to == user_id,
            models.Task.status == models.TaskStatus.in_progress,
        )
        .all()
    )

    overdue = (
        db.query(models.Task)
        .filter(
            models.Task.assigned_to == user_id,
            models.Task.deadline < now,
            models.Task.status != models.TaskStatus.completed,
        )
        .all()
    )

    completed_titles = [t.title for t in completed]
    in_progress_titles = [t.title for t in in_progress]
    overdue_titles = [t.title for t in overdue]

    prompt = (
        f"You are a productivity assistant. Generate a concise, motivating daily summary "
        f"for a team member based on their task data.\n\n"
        f"Date: {req.date or now.strftime('%Y-%m-%d')}\n"
        f"Completed today: {completed_titles or 'None'}\n"
        f"In progress: {in_progress_titles or 'None'}\n"
        f"Overdue: {overdue_titles or 'None'}\n\n"
        f"Write 2-3 short paragraphs: achievements, current focus, and a call to action for overdue items. "
        f"Be encouraging and professional."
    )
    summary_text = _call_gemini(prompt)
    return schemas.AIResponse(
        success=True,
        data={
            "summary": summary_text,
            "completed_count": len(completed),
            "in_progress_count": len(in_progress),
            "overdue_count": len(overdue),
        },
        message="Daily summary generated",
    )


@router.post("/generate-whatsapp-reply", response_model=schemas.AIResponse)
def generate_whatsapp_reply(
    req: schemas.AIWhatsAppReplyRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate a WhatsApp reply for a lead using Gemini."""
    lead = db.query(models.Lead).filter(models.Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    lead_context = (
        f"Name: {lead.name}, Source: {lead.source}, Status: {lead.status}, "
        f"Notes: {lead.notes or 'None'}"
    )
    if req.lead_context:
        lead_context += f"\nExtra context: {req.lead_context}"

    prompt = (
        f"You are a professional sales representative. Generate a helpful, friendly, and "
        f"concise WhatsApp reply to the following incoming message from a lead.\n\n"
        f"Lead Info: {lead_context}\n"
        f"Incoming Message: {req.incoming_message}\n\n"
        f"Write only the reply message text, no extra commentary. "
        f"Keep it under 200 words. Be professional and warm."
    )
    reply = _call_gemini(prompt)
    return schemas.AIResponse(
        success=True,
        data={"reply": reply, "lead_name": lead.name},
        message="WhatsApp reply generated",
    )


@router.post("/score-lead", response_model=schemas.AIResponse)
def score_lead(
    req: schemas.AIScoreLeadRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Score a lead as hot/warm/cold using Gemini and update the lead_score."""
    lead = db.query(models.Lead).filter(models.Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    prompt = (
        f"You are a CRM and sales intelligence assistant. Score the following lead.\n\n"
        f"Lead Name: {req.lead_name}\n"
        f"Source: {req.lead_source or lead.source}\n"
        f"Notes: {req.lead_notes or lead.notes or 'None'}\n"
        f"Number of Interactions: {req.interactions_count}\n"
        f"Current Status: {lead.status}\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"score": <integer 0-100>, "temperature": "hot|warm|cold", '
        f'"reasoning": "<1-2 sentences>", "recommended_action": "<brief next step>"}}'
    )
    raw = _call_gemini(prompt)
    data = _extract_json(raw)

    # Update lead score in DB if we got a numeric score
    if isinstance(data, dict) and "score" in data:
        try:
            from datetime import datetime, timezone
            lead.lead_score = int(data["score"])
            lead.updated_at = datetime.now(timezone.utc)
            db.commit()
        except (ValueError, TypeError):
            pass

    return schemas.AIResponse(success=True, data=data, message="Lead scored successfully")


@router.post("/generate-task-plan", response_model=schemas.AIResponse)
def generate_task_plan(
    req: schemas.AITaskPlanRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Generate a task plan from lead information using Gemini."""
    lead = db.query(models.Lead).filter(models.Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    prompt = (
        f"You are a sales and project management assistant. Create a practical task plan "
        f"to convert the following lead into a closed deal.\n\n"
        f"Lead Name: {req.lead_name}\n"
        f"Lead Source: {lead.source}\n"
        f"Lead Status: {lead.status}\n"
        f"Lead Notes: {req.lead_notes or lead.notes or 'None'}\n"
        f"Deal Value: {req.deal_value or 'Unknown'}\n\n"
        f"Return ONLY valid JSON with this structure:\n"
        f'{{"tasks": [{{"title": "<task title>", "description": "<details>", '
        f'"priority": "high|medium|low", "due_in_days": <integer>}}], '
        f'"strategy": "<1-2 sentence overview>"}}'
    )
    raw = _call_gemini(prompt)
    data = _extract_json(raw)
    return schemas.AIResponse(success=True, data=data, message="Task plan generated")
