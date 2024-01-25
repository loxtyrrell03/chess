import threading
import ui
import hotkeys
import Move_checker_main

def main():
    # Create an instance of SharedState
    shared_state = Move_checker_main.SharedState()

    # Start the UI in a separate thread, pass shared_state
    ui_thread = threading.Thread(target=ui.create_ui, args=(shared_state,))
    ui_thread.start()
    
    # Start the hotkey listener in a separate thread, pass shared_state
    hotkey_thread = threading.Thread(target=hotkeys.listen_for_hotkeys, args=(shared_state,))
    hotkey_thread.start()

if __name__ == "__main__":
    main()
