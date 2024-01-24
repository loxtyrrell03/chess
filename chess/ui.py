import tkinter as tk
import screen_capture
import Move_checker_main
# import shared_data
import threading

def start():
    print("Start button pressed")
    thread = threading.Thread(target=Move_checker_main.main_program)
    thread.start()

    # thread = threading.Thread(target=Move_checker_main.check_coordinates)
    # thread.start()
          
def pause():
    print("Pause button pressed")
    Move_checker_main.stop_program()

def engine_auto_play():
    print("Engine Auto-Play button pressed")
    # Add your engine auto-play functionality here

def update_board_coordinates():
    print("Update Board Coordinates button pressed")
    lh_coords = screen_capture.lh_chessboard_coordinates()
    rh_coords = screen_capture.rh_chessboard_coordinates()
    
    # shared_data.set_lh_chessboard_coordinates(lh_coords)
    # shared_data.set_rh_chessboard_coordinates(rh_coords)
    

    print("Updated LH coordinates:", lh_coords)
    print("Updated RH coordinates:", rh_coords)
    print(lh_coords, rh_coords)
    return lh_coords, rh_coords

def create_ui():
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

    # Create buttons and place them in a 2x2 grid
    start_button = tk.Button(root, text="Start", command=start)
    start_button.grid(row=0, column=0, padx=10, pady=10)

    pause_button = tk.Button(root, text="Pause", command=pause)
    pause_button.grid(row=0, column=1, padx=10, pady=10)

    engine_auto_play_button = tk.Button(root, text="Engine Auto-Play", command=engine_auto_play)
    engine_auto_play_button.grid(row=1, column=0, padx=10, pady=10)

    update_board_coordinates_button = tk.Button(root, text="Update Board Coordinates", command=update_board_coordinates)
    update_board_coordinates_button.grid(row=1, column=1, padx=10, pady=10)

    # Start the GUI event loop
    root.mainloop()
