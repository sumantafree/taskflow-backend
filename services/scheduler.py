"""
scheduler.py — APScheduler background job service for AI Task Manager.

Runs five recurring background jobs:
  1. check_overdue_tasks        — every 30 minutes
  2. check_deadline_approaching — every 1 hour
  3. check_lead_followups       — every 1 hour
  4. send_daily_summary         — daily at 18:00 UTC
  5. check_inactive_leads       — daily at 09:00 UTC

Each job opens its own SQLAlchemy session, calls EmailService /
WhatsAppService / AIService as needed, and writes Notification rows to the DB.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .ai_service import AIService
from .email_service import EmailService
from .whatsapp_service import WhatsAppService

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy model imports — avoids circular imports at module load time.
# The actual model classes are resolved once when the scheduler starts.
# ---------------------------------------------------------------------------

_Task = None
_Lead = None
_User = None
_Notification = None


def _load_models() -> None:
    """Import SQLAlchemy model classes once the app context is ready."""
    global _Task, _Lead, _User, _Notification  # noqa: PLW0603
    try:
        from ..models import Lead, Notification, Task, User  # type: ignore[import]

        _Task = Task
        _Lead = Lead
        _User = User
        _Notification = Notification
        logger.info("SchedulerService: DB models loaded successfully.")
    except ImportError as exc:
        logger.error(
            "SchedulerService: could not import models — jobs will be skipped. %s", exc
        )


# ---------------------------------------------------------------------------
# Helper: run async coroutine in a new event loop
# ---------------------------------------------------------------------------

def _run_async(coro) -> None:
    """Execute an async coroutine from a synchronous APScheduler job thread."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Helper: write a notification row to the DB
# ---------------------------------------------------------------------------

def _create_notification(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    notif_type: str = "info",
) -> None:
    """Insert a Notification record.  Silently skips if model not loaded."""
    if _Notification is None:
        return
    try:
        notif = _Notification(
            user_id=user_id,
            title=title,
            message=message,
            type=notif_type,
            is_read=False,
            created_at=datetime.utcnow(),
        )
        db.add(notif)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("Failed to create notification for user %d: %s", user_id, exc)


# ---------------------------------------------------------------------------
# SchedulerService
# ---------------------------------------------------------------------------

class SchedulerService:
    """Manages all APScheduler background jobs for the Task Manager."""

    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.email_svc = EmailService()
        self.whatsapp_svc = WhatsAppService()
        self.ai_svc = AIService()
        self._jobs_registered = False

    # ------------------------------------------------------------------
    # Public lifecycle methods
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load models, register all jobs, and start the scheduler."""
        _load_models()
        if not self._jobs_registered:
            self._register_jobs()
            self._jobs_registered = True
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("SchedulerService started. Active jobs: %d", len(self.scheduler.get_jobs()))

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("SchedulerService stopped.")

    # ------------------------------------------------------------------
    # Job registration
    # ------------------------------------------------------------------

    def _register_jobs(self) -> None:
        # Job 1 — overdue tasks: every 30 minutes
        self.scheduler.add_job(
            self._job_check_overdue_tasks,
            trigger=IntervalTrigger(minutes=30),
            id="check_overdue_tasks",
            name="Check overdue tasks",
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Job 2 — deadline approaching: every hour
        self.scheduler.add_job(
            self._job_check_deadline_approaching,
            trigger=IntervalTrigger(hours=1),
            id="check_deadline_approaching",
            name="Check tasks due within 24 h",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # Job 3 — lead follow-ups: every hour
        self.scheduler.add_job(
            self._job_check_lead_followups,
            trigger=IntervalTrigger(hours=1),
            id="check_lead_followups",
            name="Check lead follow-ups",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # Job 4 — daily summary: 18:00 UTC every day
        self.scheduler.add_job(
            self._job_send_daily_summary,
            trigger=CronTrigger(hour=18, minute=0, timezone="UTC"),
            id="send_daily_summary",
            name="Send daily summary emails",
            replace_existing=True,
        )

        # Job 5 — inactive leads: 09:00 UTC every day
        self.scheduler.add_job(
            self._job_check_inactive_leads,
            trigger=CronTrigger(hour=9, minute=0, timezone="UTC"),
            id="check_inactive_leads",
            name="Re-engage inactive leads",
            replace_existing=True,
        )

        logger.info("SchedulerService: all 5 jobs registered.")

    # ------------------------------------------------------------------
    # Job 1 — Overdue tasks (every 30 min)
    # ------------------------------------------------------------------

    def _job_check_overdue_tasks(self) -> None:
        """Find tasks past their deadline and notify the assigned user."""
        if _Task is None or _User is None:
            logger.warning("check_overdue_tasks: models not loaded, skipping.")
            return

        logger.info("JOB: check_overdue_tasks starting.")
        db: Session = SessionLocal()
        now = datetime.utcnow()

        try:
            overdue_tasks = (
                db.query(_Task)
                .filter(
                    and_(
                        _Task.deadline < now,
                        _Task.status.notin_(["completed", "cancelled"]),
                    )
                )
                .all()
            )

            logger.info("check_overdue_tasks: found %d overdue task(s).", len(overdue_tasks))

            for task in overdue_tasks:
                try:
                    user: Optional[_User] = db.query(_User).filter_by(id=task.assigned_to).first()  # type: ignore[valid-type]
                    if not user:
                        continue

                    # Email notification
                    _run_async(
                        self.email_svc.send_task_overdue(
                            user_email=user.email,
                            task_title=task.title,
                            deadline=task.deadline,
                        )
                    )

                    # WhatsApp notification (if phone available)
                    if getattr(user, "phone", None):
                        self.whatsapp_svc.send_message(
                            to_phone=user.phone,
                            message=(
                                f"⚠️ *Overdue Task*\n\n"
                                f"Your task *{task.title}* was due on "
                                f"{task.deadline.strftime('%d %b %Y at %H:%M UTC')} "
                                f"and is now overdue.\n\n"
                                f"Please update the task status or contact your manager."
                            ),
                        )

                    # DB notification record
                    _create_notification(
                        db=db,
                        user_id=user.id,
                        title=f"Task Overdue: {task.title}",
                        message=(
                            f"The task '{task.title}' was due on "
                            f"{task.deadline.strftime('%d %b %Y')} and is now overdue."
                        ),
                        notif_type="error",
                    )

                except Exception as task_exc:  # noqa: BLE001
                    logger.error(
                        "check_overdue_tasks: error processing task id=%s: %s",
                        getattr(task, "id", "?"),
                        task_exc,
                    )

        except Exception as exc:  # noqa: BLE001
            logger.error("check_overdue_tasks job error: %s", exc)
        finally:
            db.close()

        logger.info("JOB: check_overdue_tasks finished.")

    # ------------------------------------------------------------------
    # Job 2 — Deadline approaching (every 1 hour)
    # ------------------------------------------------------------------

    def _job_check_deadline_approaching(self) -> None:
        """Find tasks due within 24 hours and send reminder notifications."""
        if _Task is None or _User is None:
            logger.warning("check_deadline_approaching: models not loaded, skipping.")
            return

        logger.info("JOB: check_deadline_approaching starting.")
        db: Session = SessionLocal()
        now = datetime.utcnow()
        window_end = now + timedelta(hours=24)

        try:
            upcoming_tasks = (
                db.query(_Task)
                .filter(
                    and_(
                        _Task.deadline >= now,
                        _Task.deadline <= window_end,
                        _Task.status.notin_(["completed", "cancelled"]),
                    )
                )
                .all()
            )

            logger.info(
                "check_deadline_approaching: %d task(s) due within 24 h.",
                len(upcoming_tasks),
            )

            for task in upcoming_tasks:
                try:
                    user = db.query(_User).filter_by(id=task.assigned_to).first()
                    if not user:
                        continue

                    # Email reminder
                    _run_async(
                        self.email_svc.send_task_deadline_approaching(
                            user_email=user.email,
                            task_title=task.title,
                            deadline=task.deadline,
                        )
                    )

                    # WhatsApp reminder
                    if getattr(user, "phone", None):
                        self.whatsapp_svc.send_task_reminder(
                            phone=user.phone,
                            task_title=task.title,
                            deadline=task.deadline,
                        )

                    # DB notification record
                    hours_left = int((task.deadline - now).total_seconds() / 3600)
                    _create_notification(
                        db=db,
                        user_id=user.id,
                        title=f"Deadline Soon: {task.title}",
                        message=(
                            f"Task '{task.title}' is due in approximately "
                            f"{hours_left} hour(s)."
                        ),
                        notif_type="warning",
                    )

                except Exception as task_exc:  # noqa: BLE001
                    logger.error(
                        "check_deadline_approaching: error processing task id=%s: %s",
                        getattr(task, "id", "?"),
                        task_exc,
                    )

        except Exception as exc:  # noqa: BLE001
            logger.error("check_deadline_approaching job error: %s", exc)
        finally:
            db.close()

        logger.info("JOB: check_deadline_approaching finished.")

    # ------------------------------------------------------------------
    # Job 3 — Lead follow-ups (every 1 hour)
    # ------------------------------------------------------------------

    def _job_check_lead_followups(self) -> None:
        """Find leads with no activity in 24 hours and send follow-up messages."""
        if _Lead is None or _User is None:
            logger.warning("check_lead_followups: models not loaded, skipping.")
            return

        logger.info("JOB: check_lead_followups starting.")
        db: Session = SessionLocal()
        cutoff = datetime.utcnow() - timedelta(hours=24)

        try:
            stale_leads = (
                db.query(_Lead)
                .filter(
                    and_(
                        _Lead.status.in_(["new", "contacted"]),
                        or_(
                            _Lead.last_contacted < cutoff,
                            _Lead.last_contacted.is_(None),
                        ),
                    )
                )
                .all()
            )

            logger.info(
                "check_lead_followups: %d lead(s) need follow-up.", len(stale_leads)
            )

            for lead in stale_leads:
                try:
                    # Assigned user (if any)
                    assigned_user = (
                        db.query(_User).filter_by(id=lead.assigned_to).first()
                        if getattr(lead, "assigned_to", None)
                        else None
                    )

                    # Generate a personalised follow-up message via AI
                    conversation_history = getattr(lead, "notes", "") or ""
                    ai_message = self.ai_svc.generate_whatsapp_reply(
                        lead_name=lead.name,
                        lead_status=lead.status,
                        conversation_history=conversation_history,
                        context="Follow up on their enquiry and move them along the sales funnel.",
                    )

                    # Send WhatsApp to the lead's phone (if available)
                    if getattr(lead, "phone", None):
                        self.whatsapp_svc.send_lead_followup(
                            phone=lead.phone,
                            lead_name=lead.name,
                            message=ai_message,
                        )

                    # Notify the assigned sales rep via WhatsApp too
                    if assigned_user and getattr(assigned_user, "phone", None):
                        self.whatsapp_svc.send_message(
                            to_phone=assigned_user.phone,
                            message=(
                                f"📋 *Lead Follow-Up Sent*\n\n"
                                f"An automated follow-up was sent to lead *{lead.name}*.\n"
                                f"Status: {lead.status}\n\n"
                                f"Log in to review the conversation and take next steps."
                            ),
                        )

                    # Update last_contacted timestamp
                    lead.last_contacted = datetime.utcnow()
                    db.commit()

                    if assigned_user:
                        _create_notification(
                            db=db,
                            user_id=assigned_user.id,
                            title=f"Follow-up sent: {lead.name}",
                            message=(
                                f"An automated follow-up WhatsApp was sent to lead "
                                f"'{lead.name}' (status: {lead.status})."
                            ),
                            notif_type="info",
                        )

                except Exception as lead_exc:  # noqa: BLE001
                    db.rollback()
                    logger.error(
                        "check_lead_followups: error processing lead id=%s: %s",
                        getattr(lead, "id", "?"),
                        lead_exc,
                    )

        except Exception as exc:  # noqa: BLE001
            logger.error("check_lead_followups job error: %s", exc)
        finally:
            db.close()

        logger.info("JOB: check_lead_followups finished.")

    # ------------------------------------------------------------------
    # Job 4 — Daily summary (18:00 UTC)
    # ------------------------------------------------------------------

    def _job_send_daily_summary(self) -> None:
        """Collect today's task data, generate an AI summary, and email all users."""
        if _Task is None or _User is None:
            logger.warning("send_daily_summary: models not loaded, skipping.")
            return

        logger.info("JOB: send_daily_summary starting.")
        db: Session = SessionLocal()
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        try:
            all_users = db.query(_User).filter_by(is_active=True).all()
            logger.info("send_daily_summary: sending to %d active user(s).", len(all_users))

            for user in all_users:
                try:
                    # Gather this user's tasks for today
                    base_query = db.query(_Task).filter(_Task.assigned_to == user.id)

                    completed_tasks = (
                        base_query.filter(
                            and_(
                                _Task.status == "completed",
                                _Task.updated_at >= today_start,
                                _Task.updated_at < today_end,
                            )
                        )
                        .all()
                    )

                    pending_tasks = (
                        base_query.filter(
                            _Task.status.in_(["todo", "in_progress"])
                        )
                        .all()
                    )

                    overdue_tasks = (
                        base_query.filter(
                            and_(
                                _Task.deadline < datetime.utcnow(),
                                _Task.status.notin_(["completed", "cancelled"]),
                            )
                        )
                        .all()
                    )

                    completed_titles = [t.title for t in completed_tasks]
                    pending_titles = [t.title for t in pending_tasks]
                    overdue_titles = [t.title for t in overdue_tasks]

                    # Generate AI summary
                    summary_text = self.ai_svc.generate_daily_summary(
                        tasks_completed=completed_titles,
                        tasks_pending=pending_titles,
                        overdue_tasks=overdue_titles,
                    )

                    # Send summary email
                    _run_async(
                        self.email_svc.send_daily_summary(
                            user_email=user.email,
                            summary_text=summary_text,
                        )
                    )

                    # DB notification
                    _create_notification(
                        db=db,
                        user_id=user.id,
                        title="Your Daily Summary is Ready",
                        message=(
                            f"Completed: {len(completed_titles)} | "
                            f"Pending: {len(pending_titles)} | "
                            f"Overdue: {len(overdue_titles)}"
                        ),
                        notif_type="info",
                    )

                except Exception as user_exc:  # noqa: BLE001
                    logger.error(
                        "send_daily_summary: error for user id=%s: %s",
                        getattr(user, "id", "?"),
                        user_exc,
                    )

        except Exception as exc:  # noqa: BLE001
            logger.error("send_daily_summary job error: %s", exc)
        finally:
            db.close()

        logger.info("JOB: send_daily_summary finished.")

    # ------------------------------------------------------------------
    # Job 5 — Inactive leads (09:00 UTC)
    # ------------------------------------------------------------------

    def _job_check_inactive_leads(self) -> None:
        """Find leads inactive for 3+ days and send re-engagement WhatsApp messages."""
        if _Lead is None or _User is None:
            logger.warning("check_inactive_leads: models not loaded, skipping.")
            return

        logger.info("JOB: check_inactive_leads starting.")
        db: Session = SessionLocal()
        inactive_cutoff = datetime.utcnow() - timedelta(days=3)

        try:
            inactive_leads = (
                db.query(_Lead)
                .filter(
                    and_(
                        _Lead.status.notin_(["closed_won", "closed_lost", "cancelled"]),
                        or_(
                            _Lead.last_contacted < inactive_cutoff,
                            _Lead.last_contacted.is_(None),
                        ),
                    )
                )
                .all()
            )

            logger.info(
                "check_inactive_leads: %d inactive lead(s) found.", len(inactive_leads)
            )

            for lead in inactive_leads:
                try:
                    days_inactive = (
                        (datetime.utcnow() - lead.last_contacted).days
                        if getattr(lead, "last_contacted", None)
                        else "several"
                    )

                    re_engage_message = (
                        f"Hi {lead.name.split()[0]}, hope you're doing well! 👋\n\n"
                        f"We haven't heard from you in a while and wanted to check in. "
                        f"Are you still interested in learning more about our services?\n\n"
                        f"We'd love to help — just reply to this message or let me know "
                        f"a good time to chat. 😊"
                    )

                    if getattr(lead, "phone", None):
                        self.whatsapp_svc.send_message(
                            to_phone=lead.phone,
                            message=re_engage_message,
                        )

                    # Notify the assigned rep
                    assigned_user = (
                        db.query(_User).filter_by(id=lead.assigned_to).first()
                        if getattr(lead, "assigned_to", None)
                        else None
                    )

                    if assigned_user:
                        if getattr(assigned_user, "phone", None):
                            self.whatsapp_svc.send_message(
                                to_phone=assigned_user.phone,
                                message=(
                                    f"🔔 *Inactive Lead Alert*\n\n"
                                    f"Lead *{lead.name}* has been inactive for "
                                    f"{days_inactive} day(s).\n"
                                    f"Status: {lead.status}\n\n"
                                    f"A re-engagement message has been sent automatically. "
                                    f"Consider reaching out personally if no response."
                                ),
                            )

                        _create_notification(
                            db=db,
                            user_id=assigned_user.id,
                            title=f"Inactive Lead: {lead.name}",
                            message=(
                                f"Lead '{lead.name}' has been inactive for "
                                f"{days_inactive} day(s). "
                                f"A re-engagement message was sent via WhatsApp."
                            ),
                            notif_type="warning",
                        )

                    # Update last_contacted to avoid spamming
                    lead.last_contacted = datetime.utcnow()
                    db.commit()

                except Exception as lead_exc:  # noqa: BLE001
                    db.rollback()
                    logger.error(
                        "check_inactive_leads: error processing lead id=%s: %s",
                        getattr(lead, "id", "?"),
                        lead_exc,
                    )

        except Exception as exc:  # noqa: BLE001
            logger.error("check_inactive_leads job error: %s", exc)
        finally:
            db.close()

        logger.info("JOB: check_inactive_leads finished.")


# ---------------------------------------------------------------------------
# Module-level singleton (imported by the FastAPI app lifespan handler)
# ---------------------------------------------------------------------------

scheduler_service = SchedulerService()
