import keyboard
import arrow_detection
import Move_checker_main

def listen_for_hotkeys():
    keyboard.add_hotkey('ctrl+m', arrow_detection.ctrl_m)