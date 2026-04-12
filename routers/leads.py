from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from datetime import datetime, timezone

from database import get_db
import models
import schemas
from auth import get_current_user

router = APIRouter(prefix="/leads", tags=["Leads"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_lead_or_404(db: Session, lead_id: int) -> models.Lead:
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead


def _create_notification(db: Session, user_id: int, title: str, message: str, ntype: str = "lead"):
    db.add(models.Notification(user_id=user_id, title=title, message=message, type=ntype))


# ---------------------------------------------------------------------------
# Lead CRUD
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[schemas.LeadResponse])
def list_leads(
    status: Optional[schemas.LeadStatus] = Query(None),
    source: Optional[schemas.LeadSource] = Query(None),
    assigned_to: Optional[int] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List leads with optional filters."""
    q = db.query(models.Lead)

    if status:
        q = q.filter(models.Lead.status == status)
    if source:
        q = q.filter(models.Lead.source == source)
    if assigned_to:
        q = q.filter(models.Lead.assigned_to == assigned_to)
    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                models.Lead.name.ilike(like),
                models.Lead.email.ilike(like),
                models.Lead.phone.ilike(like),
                models.Lead.notes.ilike(like),
            )
        )

    return q.order_by(models.Lead.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/", response_model=schemas.LeadResponse, status_code=status.HTTP_201_CREATED)
def create_lead(
    lead_in: schemas.LeadCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a new lead and auto-create a follow-up task."""
    lead = models.Lead(**lead_in.model_dump())
    db.add(lead)
    db.flush()

    # Auto-create follow-up task
    follow_up_task = models.Task(
        title=f"Follow up with {lead.name}",
        description=f"New lead from {lead.source}. Phone: {lead.phone or 'N/A'}. Email: {lead.email or 'N/A'}.",
        priority=models.TaskPriority.high,
        status=models.TaskStatus.todo,
        assigned_to=lead.assigned_to,
        created_by=current_user.id,
        tags=f"lead,crm,{lead.source}",
    )
    db.add(follow_up_task)
    db.flush()

    log = models.ActivityLog(
        task_id=follow_up_task.id,
        user_id=current_user.id,
        action="auto_created_for_lead",
        new_value=lead.name,
    )
    db.add(log)

    # Notify assigned user
    if lead.assigned_to and lead.assigned_to != current_user.id:
        _create_notification(
            db,
            lead.assigned_to,
            "New Lead Assigned",
            f"You have been assigned a new lead: {lead.name}",
        )

    db.commit()
    db.refresh(lead)
    return lead


@router.get("/{lead_id}", response_model=schemas.LeadResponse)
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _get_lead_or_404(db, lead_id)


@router.put("/{lead_id}", response_model=schemas.LeadResponse)
def update_lead(
    lead_id: int,
    lead_in: schemas.LeadUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    lead = _get_lead_or_404(db, lead_id)
    update_data = lead_in.model_dump(exclude_unset=True)

    for field, val in update_data.items():
        setattr(lead, field, val)

    lead.updated_at = datetime.now(timezone.utc)

    if "assigned_to" in update_data and lead.assigned_to and lead.assigned_to != current_user.id:
        _create_notification(
            db,
            lead.assigned_to,
            "Lead Assigned to You",
            f"Lead '{lead.name}' has been assigned to you.",
        )

    db.commit()
    db.refresh(lead)
    return lead


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    lead = _get_lead_or_404(db, lead_id)
    db.delete(lead)
    db.commit()


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------

@router.get("/{lead_id}/interactions", response_model=List[schemas.InteractionResponse])
def get_interactions(
    lead_id: int,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_lead_or_404(db, lead_id)
    return (
        db.query(models.Interaction)
        .filter(models.Interaction.lead_id == lead_id)
        .order_by(models.Interaction.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.post(
    "/{lead_id}/interactions",
    response_model=schemas.InteractionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_interaction(
    lead_id: int,
    inter_in: schemas.InteractionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_lead_or_404(db, lead_id)
    interaction = models.Interaction(
        lead_id=lead_id,
        user_id=current_user.id,
        **inter_in.model_dump(),
    )
    db.add(interaction)

    # Update lead status to contacted if it was new
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if lead and lead.status == models.LeadStatus.new:
        lead.status = models.LeadStatus.contacted
        lead.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(interaction)
    return interaction


# ---------------------------------------------------------------------------
# Convert lead -> deal
# ---------------------------------------------------------------------------

@router.post("/{lead_id}/convert", response_model=schemas.DealResponse)
def convert_lead(
    lead_id: int,
    convert_req: schemas.LeadConvertRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Mark a lead as converted and create a Deal."""
    lead = _get_lead_or_404(db, lead_id)

    lead.status = models.LeadStatus.converted
    lead.updated_at = datetime.now(timezone.utc)

    deal = models.Deal(
        lead_id=lead_id,
        title=convert_req.deal_title,
        value=convert_req.deal_value,
        stage=models.DealStage.discovery,
        expected_close_date=convert_req.expected_close_date,
    )
    db.add(deal)
    db.flush()

    # Notify assignee
    if lead.assigned_to:
        _create_notification(
            db,
            lead.assigned_to,
            "Lead Converted!",
            f"Lead '{lead.name}' has been converted to a deal: {deal.title}",
            "deal",
        )

    db.commit()
    db.refresh(deal)
    return deal
