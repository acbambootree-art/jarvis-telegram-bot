import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_number = Column(String(50), unique=True, nullable=False, index=True)  # Telegram chat_id
    timezone = Column(String(50), default="Asia/Singapore")
    briefing_time = Column(String(5), default="07:00")
    google_tokens = Column(JSONB, nullable=True)  # Fernet-encrypted
    preferences = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    conversations = relationship("ConversationHistory", back_populates="user")
    tasks = relationship("Task", back_populates="user")
    notes = relationship("Note", back_populates="user")
    expenses = relationship("Expense", back_populates="user")
    reminders = relationship("Reminder", back_populates="user")
    health_metrics = relationship("HealthMetric", back_populates="user")


class ConversationHistory(Base):
    __tablename__ = "conversation_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_settings.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("UserSettings", back_populates="conversations")

    __table_args__ = (Index("ix_conversation_user_time", "user_id", "created_at"),)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_settings.id"), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    priority = Column(
        Enum("low", "medium", "high", "urgent", name="task_priority"),
        default="medium",
    )
    status = Column(
        Enum("todo", "in_progress", "done", "cancelled", name="task_status"),
        default="todo",
    )
    due_date = Column(DateTime(timezone=True), nullable=True)
    tags = Column(ARRAY(String), default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("UserSettings", back_populates="tasks")

    __table_args__ = (
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_due_date", "due_date", postgresql_where=(Column("status") != "done")),
    )


class Note(Base):
    __tablename__ = "notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_settings.id"), nullable=False)
    title = Column(String(500), default="")
    content = Column(Text, nullable=False)
    tags = Column(ARRAY(String), default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("UserSettings", back_populates="notes")

    __table_args__ = (Index("ix_notes_user", "user_id"),)


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_settings.id"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), default="SGD")
    category = Column(String(100), default="uncategorized")
    description = Column(Text, default="")
    expense_date = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("UserSettings", back_populates="expenses")

    __table_args__ = (
        Index("ix_expenses_user_date", "user_id", "expense_date"),
        Index("ix_expenses_category", "user_id", "category"),
    )


class HealthMetric(Base):
    __tablename__ = "health_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_settings.id"), nullable=False)
    metric_type = Column(String(50), nullable=False)  # steps, weight, sleep, heart_rate, etc.
    value = Column(Numeric(10, 2), nullable=False)
    unit = Column(String(20), default="")  # steps, kg, hours, bpm
    notes = Column(Text, default="")
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("UserSettings", back_populates="health_metrics")

    __table_args__ = (
        Index("ix_health_user_type_date", "user_id", "metric_type", "recorded_at"),
    )


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_settings.id"), nullable=False)
    message = Column(Text, nullable=False)
    remind_at = Column(DateTime(timezone=True), nullable=False)
    is_recurring = Column(Boolean, default=False)
    recurrence_pattern = Column(String(50), nullable=True)  # daily, weekly, monthly
    status = Column(
        Enum("pending", "sent", "cancelled", name="reminder_status"),
        default="pending",
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("UserSettings", back_populates="reminders")

    __table_args__ = (
        Index(
            "ix_reminders_pending",
            "remind_at",
            postgresql_where=(Column("status") == "pending"),
        ),
    )
