import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
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
    # The normalized (+380...) number submitted to /auth/phone, persisted only
    # once that number has actually completed a successful Telethon login (see
    # telethon_auth._finish) — not on every code request, so a mistyped/
    # abandoned attempt never overwrites a previously-connected number with
    # something unverified.
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    memberships: Mapped[list["GroupMember"]] = relationship(back_populates="user")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[GroupStatus] = mapped_column(SAEnum(GroupStatus), default=GroupStatus.ACTIVE)
    created_by_userbot: Mapped[bool] = mapped_column(Boolean, default=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # None до першого успішного group_service.sync_group (звідки б його не
    # викликали — /sync у чаті чи тиха кнопка в Mini App, POST /groups/{id}/
    # sync, app/api/routes/groups.py). Разом з created_by_userbot визначає
    # needs_sync у GroupOut: кнопка синхронізації в Mini App показується
    # лише для груп, які існували ДО підключення бота (created_by_userbot
    # =False) і ще жодного разу не звірялись — після успішної звірки
    # ховається назавжди, а не лише на поточну сесію.
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    last_message_from_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_message_text: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    awaiting_response: Mapped[bool] = mapped_column(Boolean, default=False)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    members: Mapped[list["GroupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        # One person can legitimately hold several *distinct* tags in the same
        # group (e.g. "Менеджер" + "Тімлід" — see tests/test_members_routes.py
        # ::test_tag_member_adds_a_new_tag_row), so the constraint is on the
        # full (group_id, user_id, tag) triple, not just (group_id, user_id).
        # It exists to make retrying the *same* assignment safe (a double-tap
        # on "add client" in the Mini App, or a retried /tag) — see
        # crud.add_member_tag, which catches the resulting IntegrityError and
        # treats it as an idempotent no-op instead of letting it surface.
        UniqueConstraint("group_id", "user_id", "tag", name="uq_group_members_group_user_tag"),
        Index("ix_group_members_group_id", "group_id"),
        Index("ix_group_members_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    tag: Mapped[str] = mapped_column(String(64))
    # True while a client was only sent an invite link (privacy settings blocked
    # a direct add — see app/userbot/actions.py::add_client_to_group) and hasn't
    # actually joined the chat yet. Cleared by
    # app/bot/handlers/messages.py::on_member_joined_group once Telegram
    # confirms the join. Irrelevant (always False) for staff tag rows.
    pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    group: Mapped["Group"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")
