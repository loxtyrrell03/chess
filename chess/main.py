import threading
import ui
import hotkeys

def main():
    # Start the UI in the main thread
    ui_thread = threading.Thread(target=ui.create_ui)
    ui_thread.start()
    
    hotkey_thread = threading.Thread(target=hotkeys.listen_for_hotkeys)
    hotkey_thread.start()

if __name__ == "__main__":
    main()
