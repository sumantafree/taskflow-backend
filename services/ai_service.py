"""
ai_service.py — Google Gemini AI service for AI Task Manager.
Provides task intelligence: subtask generation, priority/deadline suggestions,
daily summaries, WhatsApp reply drafting, lead scoring, task planning,
and delay prediction.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level Gemini configuration
# ---------------------------------------------------------------------------

_api_key = os.getenv("GEMINI_API_KEY", "")
if _api_key:
    genai.configure(api_key=_api_key)
else:
    logger.warning("GEMINI_API_KEY not set. AI features will not function.")

_MODEL_NAME = "gemini-1.5-flash"


# ---------------------------------------------------------------------------
# Helper: extract JSON from a model response
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Any:
    """Extract the first valid JSON object or array from *text*.

    The model sometimes wraps JSON in markdown fences — this handles that.
    Raises ValueError if no valid JSON is found.
    """
    # Strip markdown fences
    cleaned = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

    # Try the whole string first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find an embedded JSON object or array
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

    raise ValueError(f"Could not extract JSON from model response:\n{text[:500]}")


# ---------------------------------------------------------------------------
# AIService
# ---------------------------------------------------------------------------

class AIService:
    """Thin wrapper around Google Gemini for all AI-powered task management features."""

    def __init__(self) -> None:
        try:
            self.model = genai.GenerativeModel(model_name=_MODEL_NAME)
            logger.info("AIService initialised with model: %s", _MODEL_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to initialise Gemini model: %s", exc)
            self.model = None

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _call(self, prompt: str, temperature: float = 0.4) -> Optional[str]:
        """Send *prompt* to Gemini and return the raw text response.

        Returns None on any error so callers can degrade gracefully.
        """
        if not self.model:
            logger.error("Gemini model not initialised; skipping AI call.")
            return None
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=2048,
                ),
            )
            return response.text
        except Exception as exc:  # noqa: BLE001
            logger.error("Gemini API error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 1. Generate subtasks
    # ------------------------------------------------------------------

    def generate_subtasks(
        self,
        task_title: str,
        task_description: str,
    ) -> list[str]:
        """Break a task into actionable subtasks.

        Returns a list of subtask title strings (up to 8 items).
        Falls back to an empty list on error.
        """
        prompt = f"""You are a project management expert.
Given the task below, generate a list of clear, actionable subtasks that need
to be completed to finish the main task. Return ONLY a JSON array of strings.
No explanation, no markdown — just the JSON array.

Task Title: {task_title}
Task Description: {task_description}

Rules:
- Between 3 and 8 subtasks
- Each subtask should be specific and independently actionable
- Use imperative verbs (e.g., "Write", "Review", "Set up")
- Keep each subtask under 80 characters

Return format: ["subtask 1", "subtask 2", ...]"""

        raw = self._call(prompt, temperature=0.5)
        if not raw:
            return []
        try:
            subtasks = _extract_json(raw)
            if isinstance(subtasks, list):
                return [str(s) for s in subtasks if s]
            return []
        except (ValueError, TypeError) as exc:
            logger.error("Failed to parse subtasks response: %s", exc)
            return []

    # ------------------------------------------------------------------
    # 2. Suggest priority and deadline
    # ------------------------------------------------------------------

    def suggest_priority_and_deadline(
        self,
        task_title: str,
        description: str,
        existing_tasks_summary: str,
    ) -> dict:
        """Suggest a priority level and deadline for a new task.

        Returns a dict:
        {
            "priority": "low" | "medium" | "high" | "urgent",
            "deadline_days": int,          # days from today
            "suggested_deadline": str,     # ISO-8601 date string
            "reasoning": str
        }
        Falls back to sensible defaults on error.
        """
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        prompt = f"""You are a smart task scheduler.
Analyse the task and the user's current workload, then recommend a priority level
and a realistic deadline. Return ONLY a JSON object — no markdown, no explanation.

Today's date: {today_str}

New Task:
  Title: {task_title}
  Description: {description}

Current workload summary:
{existing_tasks_summary}

Return format:
{{
  "priority": "low" | "medium" | "high" | "urgent",
  "deadline_days": <integer number of days from today>,
  "suggested_deadline": "<YYYY-MM-DD>",
  "reasoning": "<1-2 sentence explanation>"
}}"""

        raw = self._call(prompt, temperature=0.3)
        default = {
            "priority": "medium",
            "deadline_days": 3,
            "suggested_deadline": (datetime.utcnow() + timedelta(days=3)).strftime(
                "%Y-%m-%d"
            ),
            "reasoning": "Default suggestion; AI service unavailable.",
        }

        if not raw:
            return default
        try:
            result = _extract_json(raw)
            # Validate required keys
            required = {"priority", "deadline_days", "suggested_deadline", "reasoning"}
            if isinstance(result, dict) and required.issubset(result.keys()):
                return result
            return default
        except (ValueError, TypeError) as exc:
            logger.error("Failed to parse priority/deadline response: %s", exc)
            return default

    # ------------------------------------------------------------------
    # 3. Generate daily summary
    # ------------------------------------------------------------------

    def generate_daily_summary(
        self,
        tasks_completed: list[str],
        tasks_pending: list[str],
        overdue_tasks: list[str],
    ) -> str:
        """Produce a human-friendly daily productivity summary.

        Returns a plain-text (markdown-aware) summary string.
        Falls back to a basic template on error.
        """
        today_str = datetime.utcnow().strftime("%B %d, %Y")
        completed_list = "\n".join(f"  - {t}" for t in tasks_completed) or "  (none)"
        pending_list = "\n".join(f"  - {t}" for t in tasks_pending) or "  (none)"
        overdue_list = "\n".join(f"  - {t}" for t in overdue_tasks) or "  (none)"

        prompt = f"""You are a professional productivity coach.
Write a concise, motivating daily summary for a team member based on their
task data below. Use plain text with light markdown (bold, bullet points).
Keep it under 250 words. Be encouraging but honest about overdue items.

Date: {today_str}

Completed tasks:
{completed_list}

Pending tasks:
{pending_list}

Overdue tasks:
{overdue_list}

Structure:
1. Brief greeting and date headline
2. Accomplishments (what was completed)
3. Still in progress
4. Overdue items and urgency note (if any)
5. One motivating closing sentence"""

        raw = self._call(prompt, temperature=0.7)
        if raw:
            return raw.strip()

        # Fallback plain-text summary
        return (
            f"Daily Summary — {today_str}\n\n"
            f"Completed: {len(tasks_completed)} task(s)\n"
            f"Pending: {len(tasks_pending)} task(s)\n"
            f"Overdue: {len(overdue_tasks)} task(s)\n\n"
            "Please review your dashboard for full details."
        )

    # ------------------------------------------------------------------
    # 4. Generate WhatsApp reply
    # ------------------------------------------------------------------

    def generate_whatsapp_reply(
        self,
        lead_name: str,
        lead_status: str,
        conversation_history: str,
        context: str,
    ) -> str:
        """Draft a natural WhatsApp reply to a lead's message.

        Returns a plain-text message string (no markdown formatting —
        WhatsApp renders asterisks literally for bold).
        Falls back to a generic acknowledgement on error.
        """
        prompt = f"""You are a professional sales assistant writing a WhatsApp reply.
Craft a warm, concise, and helpful response to the lead.

Lead Name: {lead_name}
Lead Status: {lead_status}
Context / Product info: {context}

Conversation history (most recent last):
{conversation_history}

Rules:
- Keep response under 120 words
- Sound human and natural, not robotic
- Address the lead by first name
- Use WhatsApp bold (*text*) sparingly for key points
- End with a clear next step or call-to-action
- Do NOT use excessive emojis
- Do NOT include any preamble like "Here is a reply:" — return only the message text"""

        raw = self._call(prompt, temperature=0.8)
        if raw:
            return raw.strip()

        first_name = lead_name.split()[0] if lead_name else "there"
        return (
            f"Hi {first_name}, thanks for getting in touch! "
            "I'd be happy to help. Could you share more details about what you're "
            "looking for? I'll get back to you shortly."
        )

    # ------------------------------------------------------------------
    # 5. Score lead
    # ------------------------------------------------------------------

    def score_lead(self, lead_info: dict) -> dict:
        """Score a lead and classify it as hot / warm / cold.

        lead_info keys (all optional but more = better):
            name, email, phone, source, budget, timeline,
            notes, interactions_count, last_contacted, status

        Returns:
        {
            "score": int (0-100),
            "label": "hot" | "warm" | "cold",
            "reasoning": str,
            "recommended_actions": list[str]
        }
        """
        info_text = "\n".join(
            f"  {k}: {v}" for k, v in lead_info.items() if v is not None
        )

        prompt = f"""You are a CRM expert and sales strategist.
Score the following lead from 0 to 100 based on their likelihood to convert.
Return ONLY a JSON object — no markdown, no explanation outside the JSON.

Lead Information:
{info_text}

Scoring criteria:
- Budget fit: does the lead have budget / willingness to spend? (0-25 pts)
- Timeline urgency: how soon do they need the product/service? (0-25 pts)
- Engagement level: interactions, responsiveness, source quality (0-25 pts)
- Profile completeness & intent signals: notes, specific questions (0-25 pts)

Labels:
- hot:  score 70-100 (high intent, pursue immediately)
- warm: score 40-69  (moderate interest, nurture)
- cold: score 0-39   (low interest or info, long-term nurture)

Return format:
{{
  "score": <integer 0-100>,
  "label": "hot" | "warm" | "cold",
  "reasoning": "<2-3 sentence explanation>",
  "recommended_actions": ["action 1", "action 2", "action 3"]
}}"""

        default = {
            "score": 50,
            "label": "warm",
            "reasoning": "Default score; AI service unavailable.",
            "recommended_actions": [
                "Follow up within 24 hours",
                "Qualify budget and timeline",
                "Send product information",
            ],
        }

        raw = self._call(prompt, temperature=0.2)
        if not raw:
            return default
        try:
            result = _extract_json(raw)
            required = {"score", "label", "reasoning", "recommended_actions"}
            if isinstance(result, dict) and required.issubset(result.keys()):
                # Clamp score
                result["score"] = max(0, min(100, int(result["score"])))
                return result
            return default
        except (ValueError, TypeError) as exc:
            logger.error("Failed to parse lead score response: %s", exc)
            return default

    # ------------------------------------------------------------------
    # 6. Generate task plan for a lead
    # ------------------------------------------------------------------

    def generate_task_plan(
        self,
        lead_name: str,
        service_type: str,
        notes: str,
    ) -> list[dict]:
        """Generate a structured task plan to convert a lead.

        Returns a list of task dicts:
        [
            {
                "title": str,
                "description": str,
                "priority": "low" | "medium" | "high" | "urgent",
                "deadline_days": int     # days from today
            },
            ...
        ]
        Falls back to an empty list on error.
        """
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        prompt = f"""You are a sales process expert.
Create a step-by-step task plan to successfully convert the following lead.
Return ONLY a JSON array of task objects — no markdown, no explanation.

Today's date: {today_str}
Lead Name: {lead_name}
Service/Product: {service_type}
Additional Notes: {notes}

Rules:
- Between 4 and 8 tasks ordered chronologically
- Each task must have: title, description, priority, deadline_days
- priority is one of: "low", "medium", "high", "urgent"
- deadline_days is an integer (days from today the task should be done by)
- Tasks should cover: initial contact, qualification, proposal, follow-up, close

Return format:
[
  {{
    "title": "...",
    "description": "...",
    "priority": "...",
    "deadline_days": <int>
  }},
  ...
]"""

        raw = self._call(prompt, temperature=0.4)
        if not raw:
            return []
        try:
            tasks = _extract_json(raw)
            if not isinstance(tasks, list):
                return []
            validated = []
            for t in tasks:
                if isinstance(t, dict) and "title" in t:
                    validated.append(
                        {
                            "title": str(t.get("title", "")),
                            "description": str(t.get("description", "")),
                            "priority": str(t.get("priority", "medium")),
                            "deadline_days": int(t.get("deadline_days", 3)),
                        }
                    )
            return validated
        except (ValueError, TypeError) as exc:
            logger.error("Failed to parse task plan response: %s", exc)
            return []

    # ------------------------------------------------------------------
    # 7. Predict delays
    # ------------------------------------------------------------------

    def predict_delays(
        self,
        task_title: str,
        deadline: datetime,
        workload_info: str,
    ) -> dict:
        """Predict whether a task is at risk of delay.

        Returns:
        {
            "delay_risk": "low" | "medium" | "high",
            "risk_score": int (0-100),
            "predicted_delay_days": int,    # 0 if no delay predicted
            "reasoning": str,
            "mitigation_tips": list[str]
        }
        """
        days_remaining = max(0, (deadline - datetime.utcnow()).days)
        deadline_str = deadline.strftime("%Y-%m-%d")

        prompt = f"""You are a project risk analyst.
Assess the likelihood that the following task will be delayed based on its
deadline proximity and the team's current workload. Return ONLY a JSON object.

Task: {task_title}
Deadline: {deadline_str}
Days remaining: {days_remaining}
Current workload context:
{workload_info}

Risk levels:
- low:    score 0-33   (on track, minimal risk)
- medium: score 34-66  (some risk, monitor closely)
- high:   score 67-100 (likely delayed, take action)

Return format:
{{
  "delay_risk": "low" | "medium" | "high",
  "risk_score": <int 0-100>,
  "predicted_delay_days": <int, 0 if no delay expected>,
  "reasoning": "<2-3 sentence explanation>",
  "mitigation_tips": ["tip 1", "tip 2", "tip 3"]
}}"""

        default = {
            "delay_risk": "medium",
            "risk_score": 50,
            "predicted_delay_days": 0,
            "reasoning": "Default assessment; AI service unavailable.",
            "mitigation_tips": [
                "Break the task into smaller steps",
                "Identify and address blockers early",
                "Communicate progress to stakeholders",
            ],
        }

        raw = self._call(prompt, temperature=0.3)
        if not raw:
            return default
        try:
            result = _extract_json(raw)
            required = {
                "delay_risk",
                "risk_score",
                "predicted_delay_days",
                "reasoning",
                "mitigation_tips",
            }
            if isinstance(result, dict) and required.issubset(result.keys()):
                result["risk_score"] = max(0, min(100, int(result["risk_score"])))
                result["predicted_delay_days"] = max(
                    0, int(result["predicted_delay_days"])
                )
                return result
            return default
        except (ValueError, TypeError) as exc:
            logger.error("Failed to parse delay prediction response: %s", exc)
            return default
