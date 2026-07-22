from aiogram.fsm.state import State, StatesGroup


class PlaylistCreationStates(StatesGroup):
    """States for playlist creation flow"""

    waiting_for_name = State()
    waiting_for_name_with_track = State()  # When creating playlist and adding a track
