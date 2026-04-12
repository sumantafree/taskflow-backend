from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float,
    ForeignKey, Text, Enum as SAEnum
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum

from database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    admin = "admin"
    member = "member"


class TaskStatus(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    review = "review"
    completed = "completed"


class TaskPriority(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"


class LeadSource(str, enum.Enum):
    facebook = "facebook"
    website = "website"
    whatsapp = "whatsapp"
    manual = "manual"


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    converted = "converted"
    lost = "lost"


class DealStage(str, enum.Enum):
    discovery = "discovery"
    proposal = "proposal"
    negotiation = "negotiation"
    closed_won = "closed_won"
    closed_lost = "closed_lost"


class InteractionType(str, enum.Enum):
    whatsapp = "whatsapp"
    email = "email"
    call = "call"
    note = "note"


class InteractionDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class TemplateType(str, enum.Enum):
    whatsapp = "whatsapp"
    email = "email"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole), default=UserRole.member, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    tasks_assigned = relationship("Task", foreign_keys="Task.assigned_to", back_populates="assignee")
    tasks_created = relationship("Task", foreign_keys="Task.created_by", back_populates="creator")
    activity_logs = relationship("ActivityLog", back_populates="user")
    leads_assigned = relationship("Lead", foreign_keys="Lead.assigned_to", back_populates="assignee")
    interactions = relationship("Interaction", back_populates="user")
    templates_created = relationship("MessageTemplate", back_populates="creator")
    notifications = relationship("Notification", back_populates="user")

    def __repr__(self):
        return f"<User id={self.id} email={self.email} role={self.role}>"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(SAEnum(TaskStatus), default=TaskStatus.todo, nullable=False)
    priority = Column(SAEnum(TaskPriority), default=TaskPriority.medium, nullable=False)
    deadline = Column(DateTime, nullable=True)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    tags = Column(String(500), nullable=True)  # comma-separated
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    assignee = relationship("User", foreign_keys=[assigned_to], back_populates="tasks_assigned")
    creator = relationship("User", foreign_keys=[created_by], back_populates="tasks_created")
    subtasks = relationship("Subtask", back_populates="task", cascade="all, delete-orphan")
    activity_logs = relationship("ActivityLog", back_populates="task", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Task id={self.id} title={self.title!r} status={self.status}>"


class Subtask(Base):
    __tablename__ = "subtasks"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    is_completed = Column(Boolean, default=False, nullable=False)

    # Relationships
    task = relationship("Task", back_populates="subtasks")

    def __repr__(self):
        return f"<Subtask id={self.id} task_id={self.task_id} title={self.title!r}>"


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(255), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    task = relationship("Task", back_populates="activity_logs")
    user = relationship("User", back_populates="activity_logs")

    def __repr__(self):
        return f"<ActivityLog id={self.id} task_id={self.task_id} action={self.action!r}>"


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(30), nullable=True)
    email = Column(String(255), nullable=True)
    source = Column(SAEnum(LeadSource), default=LeadSource.manual, nullable=False)
    status = Column(SAEnum(LeadStatus), default=LeadStatus.new, nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    lead_score = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    assignee = relationship("User", foreign_keys=[assigned_to], back_populates="leads_assigned")
    deals = relationship("Deal", back_populates="lead", cascade="all, delete-orphan")
    interactions = relationship("Interaction", back_populates="lead", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Lead id={self.id} name={self.name!r} status={self.status}>"


class Deal(Base):
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    value = Column(Float, default=0.0, nullable=False)
    stage = Column(SAEnum(DealStage), default=DealStage.discovery, nullable=False)
    expected_close_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    lead = relationship("Lead", back_populates="deals")

    def __repr__(self):
        return f"<Deal id={self.id} title={self.title!r} stage={self.stage} value={self.value}>"


class Interaction(Base):
    __tablename__ = "interactions"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(SAEnum(InteractionType), nullable=False)
    message = Column(Text, nullable=True)
    direction = Column(SAEnum(InteractionDirection), default=InteractionDirection.outbound, nullable=False)
    status = Column(String(50), default="sent", nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    lead = relationship("Lead", back_populates="interactions")
    user = relationship("User", back_populates="interactions")

    def __repr__(self):
        return f"<Interaction id={self.id} lead_id={self.lead_id} type={self.type}>"


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    type = Column(SAEnum(TemplateType), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    creator = relationship("User", back_populates="templates_created")

    def __repr__(self):
        return f"<MessageTemplate id={self.id} name={self.name!r} type={self.type}>"


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    type = Column(String(50), default="info", nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    user = relationship("User", back_populates="notifications")

    def __repr__(self):
        return f"<Notification id={self.id} user_id={self.user_id} title={self.title!r} is_read={self.is_read}>"
