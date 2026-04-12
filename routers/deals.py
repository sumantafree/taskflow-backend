from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
import os

from database import get_db
import models
import schemas
from auth import get_current_user

router = APIRouter(prefix="/deals", tags=["Deals"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_deal_or_404(db: Session, deal_id: int) -> models.Deal:
    deal = db.query(models.Deal).filter(models.Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deal not found")
    return deal


def _notify_stage_change(db: Session, deal: models.Deal, old_stage: str, new_stage: str):
    """Create in-app notification and attempt WhatsApp notification on stage change."""
    if deal.lead and deal.lead.assigned_to:
        notif = models.Notification(
            user_id=deal.lead.assigned_to,
            title="Deal Stage Updated",
            message=f"Deal '{deal.title}' moved from {old_stage} → {new_stage}",
            type="deal",
        )
        db.add(notif)

    # WhatsApp notification (best-effort, non-blocking)
    _try_whatsapp_deal_notification(deal, old_stage, new_stage)


def _try_whatsapp_deal_notification(deal: models.Deal, old_stage: str, new_stage: str):
    """Fire-and-forget WhatsApp message to lead's phone on deal stage change."""
    try:
        from twilio.rest import Client as TwilioClient

        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        from_number = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

        if not account_sid or not auth_token:
            return
        if not deal.lead or not deal.lead.phone:
            return

        client = TwilioClient(account_sid, auth_token)
        to_number = f"whatsapp:{deal.lead.phone}"
        message = (
            f"Hi {deal.lead.name}, great news! Your deal '{deal.title}' has progressed "
            f"from *{old_stage.replace('_', ' ').title()}* to *{new_stage.replace('_', ' ').title()}*. "
            f"We will be in touch shortly."
        )
        client.messages.create(body=message, from_=from_number, to=to_number)
    except Exception:
        # Non-critical — log silently
        pass


# ---------------------------------------------------------------------------
# Deal CRUD
# ---------------------------------------------------------------------------

@router.get("/pipeline", response_model=List[schemas.PipelineStageGroup])
def get_pipeline(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return all deals grouped by stage."""
    deals = db.query(models.Deal).order_by(models.Deal.created_at.desc()).all()
    stage_map: dict = {stage: [] for stage in models.DealStage}

    for deal in deals:
        stage_map[deal.stage].append(deal)

    result = []
    for stage, stage_deals in stage_map.items():
        total_value = sum(d.value for d in stage_deals)
        result.append(
            schemas.PipelineStageGroup(
                stage=stage,
                deals=stage_deals,
                total_value=total_value,
                count=len(stage_deals),
            )
        )
    return result


@router.get("/", response_model=List[schemas.DealResponse])
def list_deals(
    stage: Optional[schemas.DealStage] = Query(None),
    lead_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.Deal)
    if stage:
        q = q.filter(models.Deal.stage == stage)
    if lead_id:
        q = q.filter(models.Deal.lead_id == lead_id)
    return q.order_by(models.Deal.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/", response_model=schemas.DealResponse, status_code=status.HTTP_201_CREATED)
def create_deal(
    deal_in: schemas.DealCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # Verify lead exists
    lead = db.query(models.Lead).filter(models.Lead.id == deal_in.lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    deal = models.Deal(**deal_in.model_dump())
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return deal


@router.get("/{deal_id}", response_model=schemas.DealResponse)
def get_deal(
    deal_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _get_deal_or_404(db, deal_id)


@router.put("/{deal_id}", response_model=schemas.DealResponse)
def update_deal(
    deal_id: int,
    deal_in: schemas.DealUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Update deal. Stage change triggers WhatsApp notification."""
    deal = _get_deal_or_404(db, deal_id)
    update_data = deal_in.model_dump(exclude_unset=True)

    old_stage = deal.stage

    for field, val in update_data.items():
        setattr(deal, field, val)

    deal.updated_at = datetime.now(timezone.utc)

    if "stage" in update_data and update_data["stage"] != old_stage:
        _notify_stage_change(db, deal, old_stage, deal.stage)

    db.commit()
    db.refresh(deal)
    return deal


@router.delete("/{deal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deal(
    deal_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    deal = _get_deal_or_404(db, deal_id)
    db.delete(deal)
    db.commit()
