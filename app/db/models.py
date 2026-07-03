import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(enum.StrEnum):
    KERIVNYK = "Керівник"
    MANAGER = "Менеджер"
    DIAGNOST = "Діагност"
    TEAMLEAD = "Тімлід"
    SEO = "SEO"
    SALES = "Відділ продажу"
    SALES_HEAD = "Керівник відділу продажу"


NOTIFY_ROLES = {Role.KERIVNYK, Role.MANAGER, Role.DIAGNOST, Role.TEAMLEAD}

CLIENT_TAG = "Клієнт"


class GroupStatus(enum.StrEnum):
    PENDING_SETUP = "pending_setup"
    ACTIVE = "active"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[Role | None] = mapped_column(SAEnum(Role), nullable=True)
    session_string: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    memberships: Mapped[list["GroupMember"]] = relationship(back_populates="user")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[GroupStatus] = mapped_column(SAEnum(GroupStatus), default=GroupStatus.ACTIVE)
    created_by_userbot: Mapped[bool] = mapped_column(Boolean, default=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    last_message_from_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    awaiting_response: Mapped[bool] = mapped_column(Boolean, default=False)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    members: Mapped[list["GroupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class GroupMember(Base):
    __tablename__ = "group_members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    tag: Mapped[str] = mapped_column(String(64))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    group: Mapped["Group"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")
