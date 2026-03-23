from aiogram.dispatcher.filters.state import State, StatesGroup


class ManualNewsState(StatesGroup):
    waiting_for_text = State()
