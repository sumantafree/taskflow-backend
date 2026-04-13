import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("task_manager")

# ---------------------------------------------------------------------------
# Database & models
# ---------------------------------------------------------------------------
from database import engine, SessionLocal
import models

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from routers import users, tasks, leads, deals, dashboard, ai, whatsapp, notifications

# ---------------------------------------------------------------------------
# APScheduler jobs
# ---------------------------------------------------------------------------

def _check_overdue_tasks():
    """Scheduled job: notify assignees of tasks that just became overdue."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "10")))

        overdue_tasks = (
            db.query(models.Task)
            .filter(
                models.Task.deadline >= window_start,
                models.Task.deadline <= now,
                models.Task.status != models.TaskStatus.completed,
                models.Task.assigned_to.isnot(None),
            )
            .all()
        )

        for task in overdue_tasks:
            existing = (
                db.query(models.Notification)
                .filter(
                    models.Notification.user_id == task.assigned_to,
                    models.Notification.type == "overdue",
                    models.Notification.message.contains(str(task.id)),
                )
                .first()
            )
            if not existing:
                notif = models.Notification(
                    user_id=task.assigned_to,
                    title="Task Overdue",
                    message=f"Task '{task.title}' (ID: {task.id}) is now overdue.",
                    type="overdue",
                )
                db.add(notif)

        if overdue_tasks:
            db.commit()
            logger.info(f"Overdue check: {len(overdue_tasks)} task(s) flagged")
    except Exception as exc:
        logger.error(f"Overdue task scheduler error: {exc}")
    finally:
        db.close()


def _send_daily_digest():
    """Scheduled job: log that daily digest should be sent (extend with email logic)."""
    logger.info("Daily digest job triggered at %s", datetime.now(timezone.utc).isoformat())


def _start_scheduler():
    """Initialise and start APScheduler with background jobs."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger

        interval_minutes = int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "10"))

        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            _check_overdue_tasks,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="check_overdue_tasks",
            replace_existing=True,
        )
        scheduler.add_job(
            _send_daily_digest,
            trigger=CronTrigger(hour=8, minute=0),  # 08:00 UTC daily
            id="daily_digest",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("APScheduler started (overdue check every %d min)", interval_minutes)
        return scheduler
    except Exception as exc:
        logger.error(f"Failed to start scheduler: {exc}")
        return None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler

    # Create all DB tables
    logger.info("Creating database tables...")
    models.Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")

    # Start background scheduler
    _scheduler = _start_scheduler()

    yield  # Application running

    # Shutdown scheduler cleanly
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Task Manager API",
    description=(
        "Production-ready backend for the AI Task Manager app. "
        "Includes task/project management, CRM (leads & deals), "
        "WhatsApp integration, Gemini AI features, and real-time notifications."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS — raw middleware that injects headers on EVERY response
# ---------------------------------------------------------------------------
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

/* @app.middleware("http")
async def cors_middleware(request: Request, call_next):
    # Handle preflight OPTIONS immediately — no route processing needed
    if request.method == "OPTIONS":
        response = StarletteResponse(status_code=200)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept, Origin"
        response.headers["Access-Control-Max-Age"] = "3600"
        return response

    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept, Origin"
    return response */

# Keep CORSMiddleware as a fallback layer
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://taskflow.digitalsumanta.com",
        "http://localhost:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------

app.include_router(users.router)
app.include_router(tasks.router)
app.include_router(leads.router)
app.include_router(deals.router)
app.include_router(dashboard.router)
app.include_router(ai.router)
app.include_router(whatsapp.router)
app.include_router(notifications.router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.api_route("/health", methods=["GET", "HEAD"], tags=["Health"])
def health_check():
    """Liveness probe — accepts both GET and HEAD (for Render health checker)."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
    }


@app.api_route("/", methods=["GET", "HEAD"], tags=["Root"])
def root():
    return {
        "message": "AI Task Manager API is running",
        "docs": "/docs",
        "health": "/health",
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
