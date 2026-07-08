"""Tests for the in-memory self-join suppression registry
(app/services/group_creation_registry.py).

Kept isolated from Telethon/aiogram — this module is a plain in-process
`set[int]` of staff user_ids, so these tests just exercise its own tiny API
directly. See tests/test_bot_messages.py for the on_bot_added_to_group
integration and app/services/group_service.py for the producer side.
"""

from app.services import group_creation_registry as registry


def _reset() -> None:
    registry.unmark_pending(*registry._pending_actor_ids)


def setup_function() -> None:
    _reset()


def teardown_function() -> None:
    _reset()


def test_is_pending_false_for_unregistered_actor() -> None:
    assert registry.is_pending(1) is False


def test_mark_pending_makes_is_pending_true() -> None:
    registry.mark_pending(1)

    assert registry.is_pending(1) is True


def test_mark_pending_accepts_multiple_actor_ids_at_once() -> None:
    registry.mark_pending(1, 2)

    assert registry.is_pending(1) is True
    assert registry.is_pending(2) is True


def test_unmark_pending_removes_the_actor_id() -> None:
    registry.mark_pending(1)

    registry.unmark_pending(1)

    assert registry.is_pending(1) is False


def test_unmark_pending_is_a_no_op_for_an_actor_id_that_was_never_marked() -> None:
    registry.unmark_pending(999)

    assert registry.is_pending(999) is False


def test_unmark_pending_leaves_other_actor_ids_untouched() -> None:
    registry.mark_pending(1, 2)

    registry.unmark_pending(1)

    assert registry.is_pending(1) is False
    assert registry.is_pending(2) is True
