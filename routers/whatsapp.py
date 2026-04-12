from fastapi import APIRouter, Depends, HTTPException, Request, status, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
import os

from database import get_db
import models
import schemas
from auth import get_current_user

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


# ---------------------------------------------------------------------------
# Twilio helper
# ---------------------------------------------------------------------------

def _get_twilio_client():
    """Return a configured Twilio REST client."""
    try:
        from twilio.rest import Client
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="twilio package not installed",
        )
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio credentials not configured",
        )
    return Client(account_sid, auth_token)


def _whatsapp_number(phone: str) -> str:
    """Ensure phone number has whatsapp: prefix."""
    phone = phone.strip()
    if not phone.startswith("whatsapp:"):
        return f"whatsapp:{phone}"
    return phone


def _send_whatsapp(to: str, body: str) -> str:
    """Send a WhatsApp message and return the SID."""
    client = _get_twilio_client()
    from_number = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    msg = client.messages.create(
        body=body,
        from_=from_number,
        to=_whatsapp_number(to),
    )
    return msg.sid


# ---------------------------------------------------------------------------
# Send single message
# ---------------------------------------------------------------------------

@router.post("/send")
def send_message(
    req: schemas.WhatsAppSendRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Send a WhatsApp message to a phone number."""
    try:
        sid = _send_whatsapp(req.to, req.message)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return {"success": True, "message_sid": sid, "to": req.to}


# ---------------------------------------------------------------------------
# Webhook — receive incoming WhatsApp messages
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Twilio webhook for incoming WhatsApp messages.
    Parses the form-encoded payload, finds the lead by phone, and logs the interaction.
    Returns an empty TwiML response (no auto-reply).
    """
    form_data = await request.form()
    from_number = str(form_data.get("From", "")).replace("whatsapp:", "").strip()
    body = str(form_data.get("Body", "")).strip()
    message_sid = str(form_data.get("MessageSid", ""))

    if from_number:
        # Normalise: strip leading + for lookup flexibility
        phone_variants = [from_number, from_number.lstrip("+")]

        lead = (
            db.query(models.Lead)
            .filter(models.Lead.phone.in_(phone_variants))
            .first()
        )

        if lead:
            # Log interaction
            interaction = models.Interaction(
                lead_id=lead.id,
                user_id=lead.assigned_to or 1,
                type=models.InteractionType.whatsapp,
                message=body,
                direction=models.InteractionDirection.inbound,
                status="received",
            )
            db.add(interaction)

            # Update lead status if new
            if lead.status == models.LeadStatus.new:
                lead.status = models.LeadStatus.contacted
                lead.updated_at = datetime.now(timezone.utc)

            # Notify assigned user
            if lead.assigned_to:
                notif = models.Notification(
                    user_id=lead.assigned_to,
                    title=f"New WhatsApp from {lead.name}",
                    message=body[:200],
                    type="whatsapp",
                )
                db.add(notif)

            db.commit()

    # Return empty TwiML so Twilio doesn't show an error
    twiml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return Response(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# Bulk send
# ---------------------------------------------------------------------------

@router.post("/send-bulk")
def send_bulk(
    req: schemas.WhatsAppBulkSendRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Send a WhatsApp message to multiple leads."""
    results = []
    for lead_id in req.lead_ids:
        lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
        if not lead or not lead.phone:
            results.append({"lead_id": lead_id, "success": False, "error": "Lead not found or no phone"})
            continue

        try:
            sid = _send_whatsapp(lead.phone, req.message)

            # Log interaction
            interaction = models.Interaction(
                lead_id=lead_id,
                user_id=current_user.id,
                type=models.InteractionType.whatsapp,
                message=req.message,
                direction=models.InteractionDirection.outbound,
                status="sent",
            )
            db.add(interaction)
            results.append({"lead_id": lead_id, "success": True, "message_sid": sid})
        except Exception as exc:
            results.append({"lead_id": lead_id, "success": False, "error": str(exc)})

    db.commit()
    successful = sum(1 for r in results if r.get("success"))
    return {"total": len(req.lead_ids), "successful": successful, "results": results}


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@router.get("/templates", response_model=List[schemas.MessageTemplateResponse])
def list_templates(
    type: Optional[schemas.TemplateType] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.MessageTemplate)
    if type:
        q = q.filter(models.MessageTemplate.type == type)
    return q.order_by(models.MessageTemplate.created_at.desc()).all()


@router.post("/templates", response_model=schemas.MessageTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    tmpl_in: schemas.MessageTemplateCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    template = models.MessageTemplate(
        **tmpl_in.model_dump(),
        created_by=current_user.id,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


# ---------------------------------------------------------------------------
# Send template to a lead
# ---------------------------------------------------------------------------

@router.post("/send-template/{lead_id}/{template_id}")
def send_template_to_lead(
    lead_id: int,
    template_id: int,
    req: schemas.WhatsAppSendTemplateRequest = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Send a message template to a lead, substituting {{name}} placeholder.
    Optional variables dict: {"name": "John", ...}
    """
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    if not lead.phone:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Lead has no phone number")

    template = db.query(models.MessageTemplate).filter(models.MessageTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    # Substitute placeholders
    message = template.content.replace("{{name}}", lead.name)
    if req and req.variables:
        for key, val in req.variables.items():
            message = message.replace(f"{{{{{key}}}}}", str(val))

    try:
        sid = _send_whatsapp(lead.phone, message)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    # Log interaction
    interaction = models.Interaction(
        lead_id=lead_id,
        user_id=current_user.id,
        type=models.InteractionType.whatsapp,
        message=message,
        direction=models.InteractionDirection.outbound,
        status="sent",
    )
    db.add(interaction)
    db.commit()

    return {
        "success": True,
        "message_sid": sid,
        "lead_name": lead.name,
        "template_name": template.name,
    }
