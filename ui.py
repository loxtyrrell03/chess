import tkinter as tk
import screen_capture
import Move_checker_main
import threading
from Move_checker_main import shared_state
import os
import sys
import subprocess

def start(shared_state):
    print("Start button pressed")

    # Check if the thread is already running
    if shared_state.thread is not None and shared_state.thread.is_alive():
        print("A thread is already running. Stopping it first.")
        shared_state.stop_thread_flag = True
        shared_state.thread.join()  # Wait for the current thread to finish

    # Reset the flag and start a new thread
    shared_state.stop_thread_flag = False
    shared_state.thread = threading.Thread(target=Move_checker_main.main_program, args=(shared_state,))
    shared_state.thread.start()
          
def pause(shared_state):
    print("Pause button pressed")
    shared_state.stop_thread_flag = True  # Signal the thread to stop

def engine_auto_play():
    print("Engine Auto-Play button pressed")
    # Add your engine auto-play functionality here

def update_board_coordinates():
    print("Update Board Coordinates button pressed")
    lh_coords = screen_capture.lh_chessboard_coordinates()
    rh_coords = screen_capture.rh_chessboard_coordinates()

    print("Updated LH coordinates:", lh_coords)
    print("Updated RH coordinates:", rh_coords)
    print(lh_coords, rh_coords)

    # Here you would signal to main.py to terminate.
    # For example, you could set a global flag or write to a file.

    # After ensuring main.py has terminated, restart it.
    restart_program()

    return lh_coords, rh_coords

def restart_program():
    python = sys.executable
    os.execl(python, python, *sys.argv)

def create_ui(shared_state):
    # Create the main window
    root = tk.Tk()
    root.title("Control Panel")

    # Set the window to always stay on top
    root.attributes('-topmost', True)

    # Set the size of the window and position it near the center-top of the screen
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    window_width = 300  # Increased width
    window_height = 100 # Decreased height for wider aspect
    x_coordinate = (screen_width / 2) - (window_width / 2)
    y_coordinate = screen_height * 0.1
    root.geometry(f"{window_width}x{window_height}+{int(x_coordinate)}+{int(y_coordinate)}")

   # Using grid for layout
    start_button = tk.Button(root, text="Start", command=lambda: start(shared_state))
    start_button.grid(row=0, column=0, padx=10, pady=10)

    pause_button = tk.Button(root, text="Pause", command=lambda: pause(shared_state))
    pause_button.grid(row=0, column=1, padx=10, pady=10)

    engine_auto_play_button = tk.Button(root, text="Engine Auto-Play", command=lambda: engine_auto_play(shared_state))
    engine_auto_play_button.grid(row=1, column=0, padx=10, pady=10)

    update_board_coordinates_button = tk.Button(root, text="Update Board Coordinates", command=update_board_coordinates)
    update_board_coordinates_button.grid(row=1, column=1, padx=10, pady=10)

    # Start the GUI event loop
    root.mainloop()
