"""
email_service.py — Async HTML email service for AI Task Manager.
Uses aiosmtplib with SMTP credentials loaded from .env
"""

import logging
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML template helpers
# ---------------------------------------------------------------------------

_BASE_STYLE = """
  body { margin:0; padding:0; background:#f4f6f9; font-family:'Segoe UI',Arial,sans-serif; }
  .wrapper { max-width:600px; margin:32px auto; background:#ffffff;
             border-radius:10px; overflow:hidden;
             box-shadow:0 2px 12px rgba(0,0,0,.08); }
  .header  { background:linear-gradient(135deg,#4f46e5,#7c3aed);
             padding:28px 32px; text-align:center; }
  .header h1 { margin:0; color:#ffffff; font-size:22px; font-weight:700;
               letter-spacing:.5px; }
  .header p  { margin:6px 0 0; color:#c7d2fe; font-size:13px; }
  .body    { padding:32px; color:#374151; }
  .body p  { margin:0 0 16px; line-height:1.6; font-size:15px; }
  .card    { background:#f8fafc; border-left:4px solid #4f46e5;
             border-radius:6px; padding:16px 20px; margin:20px 0; }
  .card .label { font-size:11px; text-transform:uppercase; letter-spacing:1px;
                 color:#6b7280; font-weight:600; margin-bottom:4px; }
  .card .value { font-size:16px; font-weight:600; color:#1f2937; }
  .badge       { display:inline-block; padding:4px 12px; border-radius:999px;
                 font-size:12px; font-weight:600; }
  .badge-red    { background:#fee2e2; color:#dc2626; }
  .badge-orange { background:#ffedd5; color:#ea580c; }
  .badge-green  { background:#dcfce7; color:#16a34a; }
  .badge-blue   { background:#dbeafe; color:#2563eb; }
  .badge-purple { background:#ede9fe; color:#7c3aed; }
  .btn   { display:inline-block; padding:12px 28px; background:#4f46e5;
           color:#ffffff; text-decoration:none; border-radius:8px;
           font-weight:600; font-size:14px; margin-top:8px; }
  .footer { background:#f8fafc; padding:18px 32px; text-align:center;
            font-size:12px; color:#9ca3af; border-top:1px solid #e5e7eb; }
"""


def _html_wrap(header_title: str, header_sub: str, body_html: str) -> str:
    """Wrap content in the shared HTML shell."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{header_title}</title>
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>&#9654; Task Manager</h1>
      <p>{header_sub}</p>
    </div>
    <div class="body">
      {body_html}
    </div>
    <div class="footer">
      This is an automated message from AI Task Manager &mdash; please do not reply.
      &copy; {datetime.utcnow().year} Task Manager
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# EmailService
# ---------------------------------------------------------------------------

class EmailService:
    """Async SMTP email service.

    Credentials are read from environment variables:
        SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
        SMTP_FROM_EMAIL, SMTP_FROM_NAME, SMTP_USE_TLS
    """

    def __init__(self) -> None:
        self.host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.port: int = int(os.getenv("SMTP_PORT", "587"))
        self.username: str = os.getenv("SMTP_USERNAME", "")
        self.password: str = os.getenv("SMTP_PASSWORD", "")
        self.from_email: str = os.getenv("SMTP_FROM_EMAIL", self.username)
        self.from_name: str = os.getenv("SMTP_FROM_NAME", "Task Manager")
        self.use_tls: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

    # ------------------------------------------------------------------
    # Core send helper
    # ------------------------------------------------------------------

    async def _send_email(self, to: str, subject: str, html_body: str) -> bool:
        """Build a MIME message and deliver it via aiosmtplib.

        Returns True on success, False on failure (error is logged).
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self.from_name} <{self.from_email}>"
        msg["To"] = to

        # Plain-text fallback (strip tags naively)
        import re
        plain = re.sub(r"<[^>]+>", "", html_body).strip()
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                start_tls=self.use_tls,
            )
            logger.info("Email sent to %s | subject: %s", to, subject)
            return True
        except aiosmtplib.SMTPException as exc:
            logger.error("SMTP error sending to %s: %s", to, exc)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error sending email to %s: %s", to, exc)
            return False

    # ------------------------------------------------------------------
    # Public notification methods
    # ------------------------------------------------------------------

    async def send_task_assigned(
        self,
        user_email: str,
        task_title: str,
        assigned_by: str,
    ) -> bool:
        """Notify a user that a task has been assigned to them."""
        subject = f"New Task Assigned: {task_title}"
        body = _html_wrap(
            header_title="Task Assigned",
            header_sub="You have a new task waiting for you",
            body_html=f"""
            <p>Hi there,</p>
            <p>A new task has been assigned to you by <strong>{assigned_by}</strong>.
               Please review the details below and get started.</p>
            <div class="card">
              <div class="label">Task</div>
              <div class="value">{task_title}</div>
            </div>
            <div class="card">
              <div class="label">Assigned by</div>
              <div class="value">{assigned_by}</div>
            </div>
            <div class="card">
              <div class="label">Assigned on</div>
              <div class="value">{datetime.utcnow().strftime("%B %d, %Y at %H:%M UTC")}</div>
            </div>
            <p>Log in to the Task Manager to view full details and accept the task.</p>
            <p><span class="badge badge-blue">Action Required</span></p>
            """,
        )
        return await self._send_email(user_email, subject, body)

    async def send_task_deadline_approaching(
        self,
        user_email: str,
        task_title: str,
        deadline: datetime,
    ) -> bool:
        """Remind a user that a task deadline is within 24 hours."""
        deadline_str = deadline.strftime("%B %d, %Y at %H:%M UTC")
        subject = f"Deadline Approaching: {task_title}"
        body = _html_wrap(
            header_title="Deadline Approaching",
            header_sub="Your task deadline is coming up soon",
            body_html=f"""
            <p>Hi there,</p>
            <p>This is a friendly reminder that the following task is due
               <strong>within 24 hours</strong>. Please make sure to complete it on time.</p>
            <div class="card">
              <div class="label">Task</div>
              <div class="value">{task_title}</div>
            </div>
            <div class="card">
              <div class="label">Deadline</div>
              <div class="value">{deadline_str}</div>
            </div>
            <p><span class="badge badge-orange">&#9888; Due Soon</span></p>
            <p>Log in now to update your progress or request an extension.</p>
            """,
        )
        return await self._send_email(user_email, subject, body)

    async def send_task_overdue(
        self,
        user_email: str,
        task_title: str,
        deadline: datetime,
    ) -> bool:
        """Alert a user that a task has passed its deadline."""
        deadline_str = deadline.strftime("%B %d, %Y at %H:%M UTC")
        subject = f"OVERDUE: {task_title}"
        body = _html_wrap(
            header_title="Task Overdue",
            header_sub="Immediate attention required",
            body_html=f"""
            <p>Hi there,</p>
            <p>The following task has <strong>passed its deadline</strong> and is now overdue.
               Please take immediate action.</p>
            <div class="card">
              <div class="label">Task</div>
              <div class="value">{task_title}</div>
            </div>
            <div class="card">
              <div class="label">Original Deadline</div>
              <div class="value">{deadline_str}</div>
            </div>
            <div class="card">
              <div class="label">Current Time</div>
              <div class="value">{datetime.utcnow().strftime("%B %d, %Y at %H:%M UTC")}</div>
            </div>
            <p><span class="badge badge-red">&#10060; Overdue</span></p>
            <p>Please log in and either complete the task or contact your manager
               to update the deadline.</p>
            """,
        )
        return await self._send_email(user_email, subject, body)

    async def send_task_completed(
        self,
        user_email: str,
        task_title: str,
    ) -> bool:
        """Confirm that a task has been marked as completed."""
        subject = f"Task Completed: {task_title}"
        body = _html_wrap(
            header_title="Task Completed",
            header_sub="Great work — task marked as done!",
            body_html=f"""
            <p>Hi there,</p>
            <p>Congratulations! The following task has been marked as
               <strong>completed</strong>.</p>
            <div class="card">
              <div class="label">Task</div>
              <div class="value">{task_title}</div>
            </div>
            <div class="card">
              <div class="label">Completed on</div>
              <div class="value">{datetime.utcnow().strftime("%B %d, %Y at %H:%M UTC")}</div>
            </div>
            <p><span class="badge badge-green">&#10003; Completed</span></p>
            <p>Keep up the excellent work. Check your dashboard for upcoming tasks.</p>
            """,
        )
        return await self._send_email(user_email, subject, body)

    async def send_task_status_changed(
        self,
        user_email: str,
        task_title: str,
        old_status: str,
        new_status: str,
    ) -> bool:
        """Notify a user that a task's status has changed."""
        subject = f"Task Status Updated: {task_title}"

        _badge_map = {
            "todo": "badge-blue",
            "in_progress": "badge-orange",
            "completed": "badge-green",
            "cancelled": "badge-red",
            "on_hold": "badge-purple",
        }
        old_cls = _badge_map.get(old_status.lower().replace(" ", "_"), "badge-blue")
        new_cls = _badge_map.get(new_status.lower().replace(" ", "_"), "badge-green")

        body = _html_wrap(
            header_title="Task Status Changed",
            header_sub="A task you are involved in has been updated",
            body_html=f"""
            <p>Hi there,</p>
            <p>The status of the following task has been updated.</p>
            <div class="card">
              <div class="label">Task</div>
              <div class="value">{task_title}</div>
            </div>
            <div class="card">
              <div class="label">Status Change</div>
              <div class="value">
                <span class="badge {old_cls}">{old_status}</span>
                &nbsp;&#8594;&nbsp;
                <span class="badge {new_cls}">{new_status}</span>
              </div>
            </div>
            <div class="card">
              <div class="label">Updated at</div>
              <div class="value">{datetime.utcnow().strftime("%B %d, %Y at %H:%M UTC")}</div>
            </div>
            <p>Log in to the Task Manager to view the full task details.</p>
            """,
        )
        return await self._send_email(user_email, subject, body)

    async def send_daily_summary(
        self,
        user_email: str,
        summary_text: str,
    ) -> bool:
        """Send an AI-generated daily task summary to a user."""
        today_str = datetime.utcnow().strftime("%B %d, %Y")
        subject = f"Your Daily Task Summary — {today_str}"

        # Convert newlines to <br> for HTML rendering
        html_summary = summary_text.replace("\n", "<br/>")

        body = _html_wrap(
            header_title="Daily Summary",
            header_sub=f"Task summary for {today_str}",
            body_html=f"""
            <p>Hi there,</p>
            <p>Here is your AI-generated task summary for today:</p>
            <div class="card" style="border-left-color:#7c3aed;">
              {html_summary}
            </div>
            <p>Visit your dashboard to review all tasks in detail and plan your day.</p>
            <p><span class="badge badge-purple">&#128200; Daily Insights</span></p>
            """,
        )
        return await self._send_email(user_email, subject, body)

    async def send_lead_assigned(
        self,
        user_email: str,
        lead_name: str,
    ) -> bool:
        """Notify a user that a new lead has been assigned to them."""
        subject = f"New Lead Assigned: {lead_name}"
        body = _html_wrap(
            header_title="New Lead Assigned",
            header_sub="A new sales lead is waiting for your action",
            body_html=f"""
            <p>Hi there,</p>
            <p>A new lead has been assigned to you. Please reach out as soon as possible
               to maximise conversion chances.</p>
            <div class="card">
              <div class="label">Lead Name</div>
              <div class="value">{lead_name}</div>
            </div>
            <div class="card">
              <div class="label">Assigned on</div>
              <div class="value">{datetime.utcnow().strftime("%B %d, %Y at %H:%M UTC")}</div>
            </div>
            <p><span class="badge badge-purple">&#128100; New Lead</span></p>
            <p>Log in to the CRM section of Task Manager to view full lead details,
               notes, and contact information.</p>
            """,
        )
        return await self._send_email(user_email, subject, body)
