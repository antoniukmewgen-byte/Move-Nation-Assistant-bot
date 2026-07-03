from aiogram.fsm.state import State, StatesGroup


class GroupCreation(StatesGroup):
    waiting_for_title = State()


class AddClient(StatesGroup):
    choosing_group = State()
    waiting_for_contact = State()


class Connect(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()
