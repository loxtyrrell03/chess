import keyboard
import arrow_detection
import Move_checker_main
import ui
from Move_checker_main import shared_state


def listen_for_hotkeys(shared_state):
    keyboard.add_hotkey('ctrl+m', lambda: arrow_detection.ctrl_m(shared_state))
    keyboard.add_hotkey('ctrl+p', lambda: ui.pause(shared_state))
    keyboard.add_hotkey('ctrl+s', lambda: ui.start(shared_state))