"""
whatsapp_service.py — Twilio-based WhatsApp messaging service for AI Task Manager.
Handles outbound notifications, bulk messaging, and incoming message routing.
"""

import logging
import os
import re
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

load_dotenv()

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Send and receive WhatsApp messages via Twilio.

    Required environment variables:
        TWILIO_ACCOUNT_SID   — Twilio Account SID
        TWILIO_AUTH_TOKEN    — Twilio Auth Token
        TWILIO_WHATSAPP_FROM — Your WhatsApp-enabled Twilio number  (e.g. +14155238886)
    """

    # Twilio's WhatsApp URI prefix
    _WA_PREFIX = "whatsapp:"

    def __init__(self) -> None:
        self.account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.from_number: str = os.getenv("TWILIO_WHATSAPP_FROM", "")

        if not all([self.account_sid, self.auth_token, self.from_number]):
            logger.warning(
                "Twilio credentials are incomplete. "
                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM."
            )

        try:
            self.client: Optional[Client] = Client(self.account_sid, self.auth_token)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to initialise Twilio client: %s", exc)
            self.client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wa(self, phone: str) -> str:
        """Normalise a phone number to Twilio WhatsApp format.

        Accepts:
          - '+14155551234'
          - '14155551234'
          - 'whatsapp:+14155551234'  (idempotent)
        """
        phone = phone.strip()
        if phone.startswith(self._WA_PREFIX):
            return phone
        # Strip any non-digit/plus characters except leading +
        cleaned = re.sub(r"[^\d+]", "", phone)
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        return f"{self._WA_PREFIX}{cleaned}"

    def _from_wa(self) -> str:
        return self._wa(self.from_number)

    # ------------------------------------------------------------------
    # Core send
    # ------------------------------------------------------------------

    def send_message(self, to_phone: str, message: str) -> bool:
        """Send a plain WhatsApp text message.

        Returns True on success, False on failure.
        """
        if not self.client:
            logger.error("Twilio client not initialised; cannot send message.")
            return False

        to_wa = self._wa(to_phone)
        from_wa = self._from_wa()

        try:
            msg = self.client.messages.create(
                body=message,
                from_=from_wa,
                to=to_wa,
            )
            logger.info(
                "WhatsApp message sent | sid=%s | to=%s | status=%s",
                msg.sid,
                to_wa,
                msg.status,
            )
            return True
        except TwilioRestException as exc:
            logger.error(
                "Twilio error sending to %s: [%s] %s", to_wa, exc.code, exc.msg
            )
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error sending WhatsApp to %s: %s", to_wa, exc)
            return False

    # ------------------------------------------------------------------
    # Templated notification methods
    # ------------------------------------------------------------------

    def send_task_reminder(
        self,
        phone: str,
        task_title: str,
        deadline: datetime,
    ) -> bool:
        """Send a task deadline reminder via WhatsApp."""
        deadline_str = deadline.strftime("%d %b %Y, %H:%M UTC")
        message = (
            f"⏰ *Task Reminder*\n\n"
            f"Your task *{task_title}* is due on *{deadline_str}*.\n\n"
            f"Please ensure it's completed on time. "
            f"Log in to Task Manager to update progress."
        )
        return self.send_message(phone, message)

    def send_lead_followup(
        self,
        phone: str,
        lead_name: str,
        message: str,
    ) -> bool:
        """Send a personalised follow-up message to a lead."""
        full_message = (
            f"👋 *Follow-Up: {lead_name}*\n\n"
            f"{message}\n\n"
            f"_Sent by AI Task Manager_"
        )
        return self.send_message(phone, full_message)

    def send_deal_update(
        self,
        phone: str,
        deal_title: str,
        new_stage: str,
    ) -> bool:
        """Notify a team member that a deal has moved to a new pipeline stage."""
        _stage_emojis = {
            "prospect": "🔍",
            "qualified": "✅",
            "proposal": "📄",
            "negotiation": "🤝",
            "closed_won": "🏆",
            "closed_lost": "❌",
        }
        emoji = _stage_emojis.get(new_stage.lower().replace(" ", "_"), "📊")
        message = (
            f"{emoji} *Deal Update*\n\n"
            f"Deal *{deal_title}* has moved to stage:\n"
            f"➡️ *{new_stage}*\n\n"
            f"Updated on {datetime.utcnow().strftime('%d %b %Y at %H:%M UTC')}.\n"
            f"Log in to review the deal and take next steps."
        )
        return self.send_message(phone, message)

    def send_bulk_messages(
        self,
        phones: list[str],
        message: str,
    ) -> list[dict]:
        """Send the same message to multiple recipients.

        Returns a list of result dicts:
            [{"phone": str, "success": bool}, ...]
        """
        results: list[dict] = []
        for phone in phones:
            success = self.send_message(phone, message)
            results.append({"phone": phone, "success": success})
            if not success:
                logger.warning("Bulk send failed for %s", phone)
        successful = sum(1 for r in results if r["success"])
        logger.info(
            "Bulk send complete: %d/%d successful", successful, len(phones)
        )
        return results

    # ------------------------------------------------------------------
    # Incoming message processing
    # ------------------------------------------------------------------

    def process_incoming(
        self,
        from_phone: str,
        message: str,
        media_url: Optional[str] = None,
    ) -> dict:
        """Parse and route an incoming WhatsApp message.

        Returns a structured dict with intent classification so the router
        or AI service can decide how to respond.

        Return shape:
        {
            "from_phone": str,
            "message": str,
            "media_url": str | None,
            "intent": str,          # "task_query" | "lead_reply" | "status_update" | "general"
            "keywords": list[str],
            "received_at": str,     # ISO-8601 UTC
        }
        """
        message_lower = message.lower().strip()

        # Lightweight keyword-based intent detection
        task_keywords = ["task", "deadline", "overdue", "assigned", "complete", "done"]
        lead_keywords = ["lead", "interested", "price", "quote", "proposal", "call me"]
        status_keywords = ["status", "update", "progress", "report"]

        found_keywords: list[str] = []
        intent = "general"

        for kw in task_keywords:
            if kw in message_lower:
                found_keywords.append(kw)
                intent = "task_query"

        for kw in lead_keywords:
            if kw in message_lower:
                found_keywords.append(kw)
                intent = "lead_reply"

        for kw in status_keywords:
            if kw in message_lower:
                found_keywords.append(kw)
                if intent == "general":
                    intent = "status_update"

        # Normalise the sender's number
        clean_from = from_phone.replace(self._WA_PREFIX, "").strip()

        result = {
            "from_phone": clean_from,
            "message": message,
            "media_url": media_url,
            "intent": intent,
            "keywords": list(set(found_keywords)),
            "received_at": datetime.utcnow().isoformat() + "Z",
        }

        logger.info(
            "Incoming WhatsApp from %s | intent=%s | keywords=%s",
            clean_from,
            intent,
            result["keywords"],
        )
        return result
