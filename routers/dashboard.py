from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime, timezone, timedelta

from database import get_db
import models
import schemas
from auth import get_current_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ---------------------------------------------------------------------------
# Task stats
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=schemas.DashboardStats)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return task counts and per-team-member breakdown."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    total_tasks = db.query(func.count(models.Task.id)).scalar() or 0

    tasks_today = (
        db.query(func.count(models.Task.id))
        .filter(models.Task.deadline >= today_start, models.Task.deadline < today_end)
        .scalar()
        or 0
    )

    tasks_overdue = (
        db.query(func.count(models.Task.id))
        .filter(
            models.Task.deadline < now,
            models.Task.status != models.TaskStatus.completed,
        )
        .scalar()
        or 0
    )

    tasks_completed_today = (
        db.query(func.count(models.Task.id))
        .filter(
            models.Task.status == models.TaskStatus.completed,
            models.Task.updated_at >= today_start,
            models.Task.updated_at < today_end,
        )
        .scalar()
        or 0
    )

    tasks_in_progress = (
        db.query(func.count(models.Task.id))
        .filter(models.Task.status == models.TaskStatus.in_progress)
        .scalar()
        or 0
    )

    tasks_todo = (
        db.query(func.count(models.Task.id))
        .filter(models.Task.status == models.TaskStatus.todo)
        .scalar()
        or 0
    )

    tasks_review = (
        db.query(func.count(models.Task.id))
        .filter(models.Task.status == models.TaskStatus.review)
        .scalar()
        or 0
    )

    # Per-member stats
    users = db.query(models.User).filter(models.User.is_active == True).all()
    team_stats: List[schemas.TeamMemberStats] = []

    for user in users:
        assigned_count = (
            db.query(func.count(models.Task.id))
            .filter(models.Task.assigned_to == user.id)
            .scalar()
            or 0
        )
        completed_count = (
            db.query(func.count(models.Task.id))
            .filter(
                models.Task.assigned_to == user.id,
                models.Task.status == models.TaskStatus.completed,
            )
            .scalar()
            or 0
        )
        overdue_count = (
            db.query(func.count(models.Task.id))
            .filter(
                models.Task.assigned_to == user.id,
                models.Task.deadline < now,
                models.Task.status != models.TaskStatus.completed,
            )
            .scalar()
            or 0
        )
        team_stats.append(
            schemas.TeamMemberStats(
                user_id=user.id,
                name=user.name,
                email=user.email,
                tasks_assigned=assigned_count,
                tasks_completed=completed_count,
                tasks_overdue=overdue_count,
            )
        )

    return schemas.DashboardStats(
        tasks_today=tasks_today,
        tasks_overdue=tasks_overdue,
        tasks_completed_today=tasks_completed_today,
        tasks_in_progress=tasks_in_progress,
        tasks_todo=tasks_todo,
        tasks_review=tasks_review,
        total_tasks=total_tasks,
        team_stats=team_stats,
    )


# ---------------------------------------------------------------------------
# CRM stats
# ---------------------------------------------------------------------------

@router.get("/crm-stats", response_model=schemas.CRMStats)
def get_crm_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return CRM overview: leads by status, pipeline value, conversion rate."""
    total_leads = db.query(func.count(models.Lead.id)).scalar() or 0

    def lead_count(s):
        return db.query(func.count(models.Lead.id)).filter(models.Lead.status == s).scalar() or 0

    new_leads = lead_count(models.LeadStatus.new)
    contacted_leads = lead_count(models.LeadStatus.contacted)
    qualified_leads = lead_count(models.LeadStatus.qualified)
    converted_leads = lead_count(models.LeadStatus.converted)
    lost_leads = lead_count(models.LeadStatus.lost)

    conversion_rate = round((converted_leads / total_leads * 100), 2) if total_leads > 0 else 0.0

    deals_count = db.query(func.count(models.Deal.id)).scalar() or 0

    total_pipeline_value = (
        db.query(func.coalesce(func.sum(models.Deal.value), 0.0))
        .filter(models.Deal.stage.notin_([models.DealStage.closed_won, models.DealStage.closed_lost]))
        .scalar()
        or 0.0
    )

    closed_won_value = (
        db.query(func.coalesce(func.sum(models.Deal.value), 0.0))
        .filter(models.Deal.stage == models.DealStage.closed_won)
        .scalar()
        or 0.0
    )

    return schemas.CRMStats(
        total_leads=total_leads,
        new_leads=new_leads,
        contacted_leads=contacted_leads,
        qualified_leads=qualified_leads,
        converted_leads=converted_leads,
        lost_leads=lost_leads,
        conversion_rate=conversion_rate,
        total_pipeline_value=float(total_pipeline_value),
        deals_count=deals_count,
        closed_won_value=float(closed_won_value),
    )


# ---------------------------------------------------------------------------
# Recent activity
# ---------------------------------------------------------------------------

@router.get("/activity", response_model=List[schemas.ActivityLogResponse])
def get_recent_activity(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return the most recent activity log entries across all tasks."""
    return (
        db.query(models.ActivityLog)
        .order_by(models.ActivityLog.timestamp.desc())
        .limit(min(limit, 100))
        .all()
    )
