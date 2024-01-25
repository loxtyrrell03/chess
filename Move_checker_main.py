from PIL import ImageGrab
import time
import ui
import pyautogui
import screen_capture

                #This script continuously captures screenshots of the LH chessboard and detects when a move is made by comparing highlighted squares

# Get the screen size once since it won't change
screen_width, screen_height = ImageGrab.grab().size


def capture_left_side():
    # Capture the left half of the screen (chess.com)
    left_half_bbox = (0, 0, screen_width // 2, screen_height)
    return ImageGrab.grab(bbox=left_half_bbox)

  
# Capture the screen
left_image = capture_left_side()

# Define the RGB values for the start and end highlight colors
color1 = (244, 246, 128)  # Example values, replace with actual RGB values
color2 = (187, 204, 68)     # Example values, replace with actual RGB values


                        #highlighted squares code


#lh_board_top_left, lh_board_bottom_right = shared_data.get_lh_chessboard_coordinates()
#rh_board_top_left, rh_board_bottom_right = shared_data.get_rh_chessboard_coordinates()
global lh_board_bottom_right
global rh_board_bottom_right

lh_board_top_left, lh_board_bottom_right = screen_capture.lh_chessboard_coordinates()
rh_board_top_left, rh_board_bottom_right = screen_capture.rh_chessboard_coordinates()

#functions and logic to detect highlighted squares, detect changes (moves made), and perform move on RH board
def process_image(image, board_top_left, board_bottom_right):
    # print(board_top_left,board_bottom_right)
    board_width = board_bottom_right[0] - board_top_left[0]
    board_height = board_bottom_right[1] - board_top_left[1]
    square_size = min(board_width, board_height) // 8
    detected_squares = []
    inset = 25

    for i in range(8):
        for j in range(8):
            # Calculate the center position of each square
            center_x = board_top_left[0] + j * square_size + square_size // 2
            center_y = board_top_left[1] + i * square_size + square_size // 2

            # Adjust the position to check the color
            check_x = center_x - inset
            check_y = center_y - inset

            # Get the pixel color at the adjusted location
            pixel_color = image.getpixel((check_x, check_y))

            if pixel_color == (244, 246, 128) or pixel_color == (187, 204, 68):
                # Convert to chess notation
                square_notation = chr(97 + j) + str(8 - i)
                detected_squares.append(square_notation)

    if len(detected_squares) == 1 or len(detected_squares) == 3:
        detected_squares = []
    
    # print(detected_squares)
    return detected_squares

def detect_changes(previous, current):
    if previous != current:
        return True
    else:
        return False
    


# Return to the initial mouse position
def is_piece_on_square(square):  
    x1, y1 = lh_board_top_left
    x2, y2 = lh_board_bottom_right

    # Calculate width and height of the chessboard
    board_width = x2 - x1
    board_height = y2 - y1

    # Calculate square size (assuming the board is a perfect square)
    square_size = min(board_width, board_height) // 8

    highlight_colors = ((244, 246, 128), (187, 204, 68))  # Replace with actual RGB values

    # Convert the chess square to screen coordinates
    col = ord(square[0]) - ord('a')
    row = 8 - int(square[1])

    center_x = x1 + col * square_size + square_size // 2
    center_y = y1 + row * square_size + square_size // 2

    check_y = center_y + 15  # Adjust y-coordinate slightly downwards

    pixel_color = capture_left_side().getpixel((center_x, check_y))

    return pixel_color not in highlight_colors


    #logic to map RH board coordinates to chess squares
def get_board_coordinates():
    # Fetch the latest coordinates
    # rh_board_top_left, rh_board_bottom_right = screen_capture.rh_chessboard_coordinates()
    # print(rh_board_top_left, rh_board_bottom_right)

    # Calculate the total width and height of the chessboard
    total_width = rh_board_bottom_right[0] - rh_board_top_left[0]
    total_height = rh_board_bottom_right[1] - rh_board_top_left[1]

    # Calculate the size of each square
    square_size = min(total_width, total_height) // 8

    board_coordinates = {}

    for i in range(8):  # 8 rows
        for j in range(8):  # 8 columns
            # Calculate the square's center point
            center_x = rh_board_top_left[0] + j * square_size + square_size // 2
            center_y = rh_board_top_left[1] + i * square_size + square_size // 2

            square_notation = chr(97 + j) + str(8 - i)
            board_coordinates[square_notation] = (center_x, center_y)

    return board_coordinates

board_coordinates = get_board_coordinates()



def click_square(square):
        pyautogui.moveTo(board_coordinates[square])
        pyautogui.click()
    
def make_move(start_square, end_square):
     initial_mouse_position = pyautogui.position()
     click_square(start_square)
     click_square(end_square)
     pyautogui.moveTo(initial_mouse_position) 


# def check_coordinates():
#     while True:
#         print("shared data:")
#         print(shared_data.get_lh_chessboard_coordinates, shared_data.get_rh_chessboard_coordinates)
#         print(lh_board_top_left,lh_board_bottom_right)
#         time.sleep(1)
     


# Initial states for comparison, before move is made
     
global previous_state
previous_state = process_image(left_image,lh_board_top_left,lh_board_bottom_right)



class SharedState:
    def __init__(self):
        self.stop_thread_flag = False
        self.thread = None

shared_state = SharedState()

# def stop_program():
#     global is_running
#     is_running = False

def main_program(shared_state):
    global previous_state
    
    while not shared_state.stop_thread_flag:
            
           
            
            # lh_board_top_left, lh_board_bottom_right = screen_capture.lh_chessboard_coordinates()   
            # rh_board_top_left, rh_board_bottom_right = screen_capture.rh_chessboard_coordinates()
            # print(lh_board_top_left, lh_board_bottom_right, rh_board_top_left, rh_board_bottom_right)
            left_image = capture_left_side()
            current_state = process_image(left_image,lh_board_top_left,lh_board_bottom_right)
            # print(lh_board_top_left, lh_board_bottom_right)

            # If changes detected, perform necessary actions
            if detect_changes(previous_state, current_state) and len(current_state) == 2:
                start_square, end_square = current_state

                if is_piece_on_square(end_square):
                    make_move(start_square, end_square)
                    print(f"Move made: {start_square} to {end_square}")
                else:
                    # Move mouse end - start 
                    make_move(end_square, start_square)
                    print(f"Move made: {end_square} to {start_square}")
                
                
                # Update previous_state AFTER the move
                previous_state = current_state
                time.sleep(0.1)
        
            else:
                time.sleep(0.1)

