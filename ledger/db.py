from datetime import datetime, date, timedelta
from typing import Optional, List, Any
from sqlalchemy import (
    create_engine,
    String,
    Text,
    Integer,
    Date,
    DateTime,
    ForeignKey,
    JSON,
    UniqueConstraint,
    delete,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
    Session,
)
from ledger.util import utcnow


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    repo_paths: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_project_owner_name"),)

    tasks: Mapped[List["Task"]] = relationship("Task", back_populates="project", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="task")  # bug | enhancement | task
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open | in_progress | done | dropped
    priority: Mapped[str] = mapped_column(String(20), default="normal")  # low | normal | high
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notion_page_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notion_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="tasks")
    events: Mapped[List["Event"]] = relationship("Event", back_populates="task", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # created | edited | done | reopened | deleted
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    task: Mapped["Task"] = relationship("Task", back_populates="events")


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # add | done | checkin
    payload: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Checkin(Base):
    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    nudged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


def get_engine(url: str, **kwargs):
    return create_engine(url, **kwargs)


def get_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def prune_pending_actions(session: Session, days: int = 14) -> int:
    cutoff = utcnow() - timedelta(days=days)
    stmt = delete(PendingAction).where(PendingAction.created_at < cutoff)
    result = session.execute(stmt)
    session.commit()
    return result.rowcount
