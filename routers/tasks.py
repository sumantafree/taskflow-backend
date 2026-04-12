from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from datetime import datetime, timezone

from database import get_db
import models
import schemas
from auth import get_current_user

router = APIRouter(prefix="/tasks", tags=["Tasks"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_activity(
    db: Session,
    task_id: int,
    user_id: int,
    action: str,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
):
    log = models.ActivityLog(
        task_id=task_id,
        user_id=user_id,
        action=action,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(log)


def _create_notification(db: Session, user_id: int, title: str, message: str, ntype: str = "task"):
    notif = models.Notification(
        user_id=user_id,
        title=title,
        message=message,
        type=ntype,
    )
    db.add(notif)


def _get_task_or_404(db: Session, task_id: int) -> models.Task:
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[schemas.TaskResponse])
def list_tasks(
    status: Optional[schemas.TaskStatus] = Query(None),
    priority: Optional[schemas.TaskPriority] = Query(None),
    assigned_to: Optional[int] = Query(None),
    search: Optional[str] = Query(None, max_length=100),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """List tasks with optional filters."""
    q = db.query(models.Task)

    if status:
        q = q.filter(models.Task.status == status)
    if priority:
        q = q.filter(models.Task.priority == priority)
    if assigned_to:
        q = q.filter(models.Task.assigned_to == assigned_to)
    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                models.Task.title.ilike(like),
                models.Task.description.ilike(like),
                models.Task.tags.ilike(like),
            )
        )

    return q.order_by(models.Task.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/", response_model=schemas.TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    task_in: schemas.TaskCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a new task."""
    task = models.Task(**task_in.model_dump(), created_by=current_user.id)
    db.add(task)
    db.flush()

    _log_activity(db, task.id, current_user.id, "created", new_value=task.title)

    # Notify assignee if different from creator
    if task.assigned_to and task.assigned_to != current_user.id:
        _create_notification(
            db,
            task.assigned_to,
            "New Task Assigned",
            f"You have been assigned: {task.title}",
        )

    db.commit()
    db.refresh(task)
    return task


@router.get("/{task_id}", response_model=schemas.TaskResponse)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _get_task_or_404(db, task_id)


@router.put("/{task_id}", response_model=schemas.TaskResponse)
def update_task(
    task_id: int,
    task_in: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Update task fields and log changes."""
    task = _get_task_or_404(db, task_id)
    update_data = task_in.model_dump(exclude_unset=True)

    for field, new_val in update_data.items():
        old_val = getattr(task, field)
        if old_val != new_val:
            _log_activity(
                db, task.id, current_user.id,
                f"updated_{field}",
                old_value=str(old_val),
                new_value=str(new_val),
            )
        setattr(task, field, new_val)

    task.updated_at = datetime.now(timezone.utc)

    # Notify new assignee
    if "assigned_to" in update_data and task.assigned_to and task.assigned_to != current_user.id:
        _create_notification(
            db,
            task.assigned_to,
            "Task Assigned to You",
            f"You have been assigned: {task.title}",
        )

    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    task = _get_task_or_404(db, task_id)
    db.delete(task)
    db.commit()


# ---------------------------------------------------------------------------
# Subtasks
# ---------------------------------------------------------------------------

@router.post("/{task_id}/subtasks", response_model=schemas.SubtaskResponse, status_code=status.HTTP_201_CREATED)
def create_subtask(
    task_id: int,
    sub_in: schemas.SubtaskCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_task_or_404(db, task_id)
    subtask = models.Subtask(task_id=task_id, title=sub_in.title)
    db.add(subtask)
    _log_activity(db, task_id, current_user.id, "added_subtask", new_value=sub_in.title)
    db.commit()
    db.refresh(subtask)
    return subtask


@router.put("/{task_id}/subtasks/{sub_id}", response_model=schemas.SubtaskResponse)
def update_subtask(
    task_id: int,
    sub_id: int,
    sub_in: schemas.SubtaskUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_task_or_404(db, task_id)
    subtask = db.query(models.Subtask).filter(
        models.Subtask.id == sub_id,
        models.Subtask.task_id == task_id,
    ).first()
    if not subtask:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subtask not found")

    update_data = sub_in.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        setattr(subtask, field, val)

    if "is_completed" in update_data:
        action = "completed_subtask" if update_data["is_completed"] else "uncompleted_subtask"
        _log_activity(db, task_id, current_user.id, action, new_value=subtask.title)

    db.commit()
    db.refresh(subtask)
    return subtask


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

@router.get("/{task_id}/activity", response_model=List[schemas.ActivityLogResponse])
def get_task_activity(
    task_id: int,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_task_or_404(db, task_id)
    return (
        db.query(models.ActivityLog)
        .filter(models.ActivityLog.task_id == task_id)
        .order_by(models.ActivityLog.timestamp.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Move (status change with notification)
# ---------------------------------------------------------------------------

@router.put("/{task_id}/move", response_model=schemas.TaskResponse)
def move_task(
    task_id: int,
    move_req: schemas.TaskMoveRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Change task status, log activity, and notify assignee."""
    task = _get_task_or_404(db, task_id)
    old_status = task.status

    if old_status == move_req.status:
        return task

    task.status = move_req.status
    task.updated_at = datetime.now(timezone.utc)

    _log_activity(
        db, task.id, current_user.id,
        "moved",
        old_value=old_status,
        new_value=move_req.status,
    )

    # Notify assignee (if different from the person moving the task)
    if task.assigned_to and task.assigned_to != current_user.id:
        _create_notification(
            db,
            task.assigned_to,
            "Task Status Updated",
            f"Task '{task.title}' moved from {old_status} → {move_req.status}",
        )

    db.commit()
    db.refresh(task)
    return task
