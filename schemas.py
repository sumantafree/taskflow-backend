from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Any
from datetime import datetime
from enum import Enum


# ---------------------------------------------------------------------------
# Enums (mirror models.py for Pydantic)
# ---------------------------------------------------------------------------

class UserRole(str, Enum):
    admin = "admin"
    member = "member"


class TaskStatus(str, Enum):
    todo = "todo"
    in_progress = "in_progress"
    review = "review"
    completed = "completed"


class TaskPriority(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class LeadSource(str, Enum):
    facebook = "facebook"
    website = "website"
    whatsapp = "whatsapp"
    manual = "manual"


class LeadStatus(str, Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    converted = "converted"
    lost = "lost"


class DealStage(str, Enum):
    discovery = "discovery"
    proposal = "proposal"
    negotiation = "negotiation"
    closed_won = "closed_won"
    closed_lost = "closed_lost"


class InteractionType(str, Enum):
    whatsapp = "whatsapp"
    email = "email"
    call = "call"
    note = "note"


class InteractionDirection(str, Enum):
    inbound = "inbound"
    outbound = "outbound"


class TemplateType(str, Enum):
    whatsapp = "whatsapp"
    email = "email"


# ---------------------------------------------------------------------------
# Auth / User schemas
# ---------------------------------------------------------------------------

class UserBase(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    role: UserRole = UserRole.member


class UserCreate(BaseModel):
    """Accepts either full_name or name so both old and new clients work."""
    full_name: Optional[str] = Field(None, min_length=1, max_length=100)
    name: Optional[str] = Field(None, min_length=1, max_length=100)  # fallback alias
    email: EmailStr
    role: UserRole = UserRole.member
    password: str = Field(..., min_length=6)

    @field_validator("full_name", mode="before")
    @classmethod
    def resolve_full_name(cls, v, info):
        # if full_name not provided, fall back to name
        if not v:
            return info.data.get("name") or v
        return v


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6)


class UserResponse(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class TokenData(BaseModel):
    user_id: Optional[int] = None
    email: Optional[str] = None


# ---------------------------------------------------------------------------
# Subtask schemas
# ---------------------------------------------------------------------------

class SubtaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class SubtaskCreate(SubtaskBase):
    pass


class SubtaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    is_completed: Optional[bool] = None


class SubtaskResponse(SubtaskBase):
    id: int
    task_id: int
    is_completed: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Task schemas
# ---------------------------------------------------------------------------

class TaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.todo
    priority: TaskPriority = TaskPriority.medium
    deadline: Optional[datetime] = None
    assigned_to: Optional[int] = None
    tags: Optional[str] = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    deadline: Optional[datetime] = None
    assigned_to: Optional[int] = None
    tags: Optional[str] = None


class TaskMoveRequest(BaseModel):
    status: TaskStatus


class TaskResponse(TaskBase):
    id: int
    created_by: int
    created_at: datetime
    updated_at: datetime
    subtasks: List[SubtaskResponse] = []
    assignee: Optional[UserResponse] = None
    creator: Optional[UserResponse] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# ActivityLog schemas
# ---------------------------------------------------------------------------

class ActivityLogResponse(BaseModel):
    id: int
    task_id: int
    user_id: int
    action: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    timestamp: datetime
    user: Optional[UserResponse] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Lead schemas
# ---------------------------------------------------------------------------

class LeadBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=30)
    email: Optional[EmailStr] = None
    source: LeadSource = LeadSource.manual
    status: LeadStatus = LeadStatus.new
    assigned_to: Optional[int] = None
    notes: Optional[str] = None
    lead_score: int = Field(0, ge=0, le=100)


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=30)
    email: Optional[EmailStr] = None
    source: Optional[LeadSource] = None
    status: Optional[LeadStatus] = None
    assigned_to: Optional[int] = None
    notes: Optional[str] = None
    lead_score: Optional[int] = Field(None, ge=0, le=100)


class LeadResponse(LeadBase):
    id: int
    created_at: datetime
    updated_at: datetime
    assignee: Optional[UserResponse] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Deal schemas
# ---------------------------------------------------------------------------

class DealBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    value: float = Field(0.0, ge=0)
    stage: DealStage = DealStage.discovery
    expected_close_date: Optional[datetime] = None


class DealCreate(DealBase):
    lead_id: int


class DealUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    value: Optional[float] = Field(None, ge=0)
    stage: Optional[DealStage] = None
    expected_close_date: Optional[datetime] = None


class DealResponse(DealBase):
    id: int
    lead_id: int
    created_at: datetime
    updated_at: datetime
    lead: Optional[LeadResponse] = None

    model_config = {"from_attributes": True}


class PipelineStageGroup(BaseModel):
    stage: DealStage
    deals: List[DealResponse]
    total_value: float
    count: int


# ---------------------------------------------------------------------------
# Interaction schemas
# ---------------------------------------------------------------------------

class InteractionBase(BaseModel):
    type: InteractionType
    message: Optional[str] = None
    direction: InteractionDirection = InteractionDirection.outbound
    status: str = "sent"


class InteractionCreate(InteractionBase):
    pass


class InteractionResponse(InteractionBase):
    id: int
    lead_id: int
    user_id: int
    created_at: datetime
    user: Optional[UserResponse] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# MessageTemplate schemas
# ---------------------------------------------------------------------------

class MessageTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1)
    type: TemplateType


class MessageTemplateCreate(MessageTemplateBase):
    pass


class MessageTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    content: Optional[str] = None
    type: Optional[TemplateType] = None


class MessageTemplateResponse(MessageTemplateBase):
    id: int
    created_by: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Notification schemas
# ---------------------------------------------------------------------------

class NotificationBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    message: str
    type: str = "info"


class NotificationCreate(NotificationBase):
    user_id: int


class NotificationResponse(NotificationBase):
    id: int
    user_id: int
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Dashboard schemas
# ---------------------------------------------------------------------------

class TeamMemberStats(BaseModel):
    user_id: int
    name: str
    email: str
    tasks_assigned: int
    tasks_completed: int
    tasks_overdue: int


class DashboardStats(BaseModel):
    tasks_today: int
    tasks_overdue: int
    tasks_completed_today: int
    tasks_in_progress: int
    tasks_todo: int
    tasks_review: int
    total_tasks: int
    team_stats: List[TeamMemberStats] = []


class CRMStats(BaseModel):
    total_leads: int
    new_leads: int
    contacted_leads: int
    qualified_leads: int
    converted_leads: int
    lost_leads: int
    conversion_rate: float
    total_pipeline_value: float
    deals_count: int
    closed_won_value: float


# ---------------------------------------------------------------------------
# WhatsApp schemas
# ---------------------------------------------------------------------------

class WhatsAppSendRequest(BaseModel):
    to: str = Field(..., description="Phone number with country code, e.g. +1234567890")
    message: str = Field(..., min_length=1)


class WhatsAppBulkSendRequest(BaseModel):
    lead_ids: List[int]
    message: str = Field(..., min_length=1)


class WhatsAppSendTemplateRequest(BaseModel):
    variables: Optional[dict] = None


class WhatsAppWebhookPayload(BaseModel):
    From: Optional[str] = None
    To: Optional[str] = None
    Body: Optional[str] = None
    MessageSid: Optional[str] = None
    AccountSid: Optional[str] = None
    NumMedia: Optional[str] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# AI schemas
# ---------------------------------------------------------------------------

class AIRequest(BaseModel):
    task_id: Optional[int] = None
    lead_id: Optional[int] = None
    context: Optional[str] = None
    extra: Optional[dict] = None


class AIGenerateSubtasksRequest(BaseModel):
    task_title: str
    task_description: Optional[str] = None


class AISuggestPriorityRequest(BaseModel):
    task_title: str
    task_description: Optional[str] = None
    deadline_hint: Optional[str] = None


class AIDailySummaryRequest(BaseModel):
    user_id: Optional[int] = None
    date: Optional[str] = None


class AIWhatsAppReplyRequest(BaseModel):
    lead_id: int
    incoming_message: str
    lead_context: Optional[str] = None


class AIScoreLeadRequest(BaseModel):
    lead_id: int
    lead_name: str
    lead_source: Optional[str] = None
    lead_notes: Optional[str] = None
    interactions_count: Optional[int] = 0


class AITaskPlanRequest(BaseModel):
    lead_id: int
    lead_name: str
    lead_notes: Optional[str] = None
    deal_value: Optional[float] = None


class AIResponse(BaseModel):
    success: bool
    data: Any
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Lead convert schema
# ---------------------------------------------------------------------------

class LeadConvertRequest(BaseModel):
    deal_title: str = Field(..., min_length=1, max_length=255)
    deal_value: float = Field(0.0, ge=0)
    expected_close_date: Optional[datetime] = None
