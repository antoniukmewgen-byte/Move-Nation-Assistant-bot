"""Реєстр user_id співробітників, які саме зараз створюють групу через застосунок.

`app/userbot/actions.py::create_group_with_team` створює нову групу через
Telethon (окремим акаунтом співробітника), а не запрошенням бота ззовні —
але з погляду Bot API це виглядає як два окремі, цілком легітимні "join":
- одна подія для щойно створеної базової групи (`CreateChatRequest`);
- ще одна для пост-міграційної супергрупи, коли бота промоутять в адміни
  (`EditAdminRequest`) — Bot API ще не бачив жодної події для цього
  chat_id, тож і промоут виглядає як "не був учасником -> учасник".

`app/bot/handlers/messages.py::on_bot_added_to_group` реагує на обидві й
надсилає вітальне повідомлення двічі, хоча жодної зовнішньої дії не було.

Перша версія цього реєстру позначала сам chat_id (дізнавались про нього вже
після `CreateChatRequest`/`MigrateChatRequest`) — і на практиці програвала
гонку: Bot API доставляє свою `my_chat_member`-подію окремим шляхом (polling),
який не чекає на відповідь нашого MTProto-виклику, тож обробник встигав
спрацювати ще до виклику `mark_pending`. Натомість тут реєструється
**user_id співробітника, чиєю Telethon-сесією виконується дія**
(`app/services/group_service.py::create_group`'s `user_id`) — це відомо
одразу, ще до першого мережевого виклику, тож гонки немає: `mark_pending`
викликається перш ніж узагалі щось піти до Telegram. `ChatMemberUpdated`
завжди містить `from_user` — того, хто фактично викликав зміну статусу бота
(див. Bot API), і саме цей user_id для дій нашого власного флоу збігається з
`user_id`, яким його тут позначено.

Звичайний `set[int]` у пам'яті процесу є достатнім: проєкт і так розрахований
рівно на один процес одночасно (див. README.md, "⚠️ Лише один інстанс"), як і
`app/services/telethon_auth.py`'s pending-auth стан.
"""

_pending_actor_ids: set[int] = set()


def mark_pending(*actor_user_ids: int) -> None:
    """Позначає user_id як "зараз створює групу через застосунок"."""
    _pending_actor_ids.update(actor_user_ids)


def unmark_pending(*actor_user_ids: int) -> None:
    """Знімає позначку після завершення (успішного чи ні) створення групи."""
    _pending_actor_ids.difference_update(actor_user_ids)


def is_pending(actor_user_id: int) -> bool:
    return actor_user_id in _pending_actor_ids
