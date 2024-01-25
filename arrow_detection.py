import pyautogui
import cv2
import numpy as np
from screen_capture import rh_chessboard_coordinates, lh_chessboard_coordinates
import time
import ui
from Move_checker_main import shared_state

#this script captures the chessboard on the RH side, detects the best move arrow, and returns the move suggested by the arrow.

top_left, bottom_right = rh_chessboard_coordinates()
LH_top_left, LH_bottom_right = lh_chessboard_coordinates()

def calculate_square_size(top_left, bottom_right):
    

    # Calculate the width and height of the chessboard
    chessboard_width = bottom_right[0] - top_left[0]
    chessboard_height = bottom_right[1] - top_left[1]

    # Calculate the size of each square
    square_size_x = chessboard_width / 8
    square_size_y = chessboard_height / 8

    return square_size_x, square_size_y

# Get the size of each chess square
square_size_x, square_size_y = calculate_square_size(top_left, bottom_right)
LH_square_size_x, LH_square_size_y = calculate_square_size(LH_top_left, LH_bottom_right)

# Function to capture a specific region of the screen
def capture_screen(top_left, bottom_right):
    width = bottom_right[0] - top_left[0]
    height = bottom_right[1] - top_left[1]
    screenshot = pyautogui.screenshot(region=(top_left[0], top_left[1], width, height))
    frame = np.array(screenshot)
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return frame




def pixel_to_chess_square(point, top_left, square_size_x, square_size_y):
    # Adjust point coordinates relative to the whole screen
    adjusted_x = point[0] + top_left[0]
    adjusted_y = point[1] + top_left[1]

    # Calculate the x and y offsets from the top left corner of the chessboard
    x_offset = adjusted_x - top_left[0]
    y_offset = adjusted_y - top_left[1]
    
    # Calculate the square indices
    square_x = x_offset // square_size_x
    square_y = 7 - (y_offset // square_size_y)  # Flip the y-coordinate for White's perspective

    # Convert to chess notation (files a-h, ranks 1-8)
    rank = str(int(square_y) + 1)
    file = chr(ord('a') + int(square_x))
    return file + rank

def chess_square_to_pixel(square, top_left, square_size_x, square_size_y):
    # Convert the file (letter) to an x-coordinate index (0-7)
    file = square[0]
    square_x = ord(file) - ord('a')

    # Convert the rank (number) to a y-coordinate index (0-7)
    rank = int(square[1])
    square_y = 8 - rank  # Flip the y-coordinate for White's perspective

    # Calculate the center of the square
    center_x = top_left[0] + (square_x * square_size_x) + (square_size_x // 2)
    center_y = top_left[1] + (square_y * square_size_y) + (square_size_y // 2)

    return center_x, center_y

# Function to detect the green arrow
#doesn't account for when arrow is completely flat, change code so it takes the widest coordinates not the highest and l
def detect_green_arrow(frame):
       # Convert the frame to HSV color space
    hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # Define a broader range for green in HSV
    lower_green = np.array([40, 40, 40])  # Hue, Saturation, Value
    upper_green = np.array([80, 255, 255])  # Adjust these values based on the arrow's color

    
    # Create a mask to only keep the green parts within the range in the HSV image
    mask = cv2.inRange(hsv_frame, lower_green, upper_green)

    # Apply morphological operations to clean up the mask
    kernel = np.ones((5,5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # Debug: Visualize the mask
    # cv2.imshow('Mask', mask)
    # cv2.waitKey(0)
    # cv2.destroyAllWindows()

    # Find the indices of the mask where there are green pixels
    green_points = np.column_stack(np.where(mask > 0))

      
    if green_points.size > 0:
        # Calculate the center of mass of the green points
        com = np.mean(green_points, axis=0)

        # Find highest and lowest points based on y-coordinate
        highest_point = green_points[green_points[:, 0].argmin()]
        lowest_point = green_points[green_points[:, 0].argmax()]

        # Convert points to (x, y) format
        highest_point = tuple(highest_point[::-1])
        lowest_point = tuple(lowest_point[::-1])

        # Determine if they are on the same square
        highest_square = pixel_to_chess_square(highest_point, top_left, square_size_x, square_size_y)
        lowest_square = pixel_to_chess_square(lowest_point, top_left, square_size_x, square_size_y)

        if highest_square == lowest_square:
            # If on the same square, find the widest points
            leftmost_point = green_points[green_points[:, 1].argmin()]
            rightmost_point = green_points[green_points[:, 1].argmax()]
            start_point, end_point = tuple(leftmost_point[::-1]), tuple(rightmost_point[::-1])
        else:
            # If on different squares, use highest and lowest points
            start_point, end_point = highest_point, lowest_point

        # Determine which point is closer to the COM
        distance_to_com_start = np.linalg.norm(np.array(start_point) - com)
        distance_to_com_end = np.linalg.norm(np.array(end_point) - com)

        # Assign the end square based on proximity to COM
        if distance_to_com_start > distance_to_com_end:
            Astart_square, Aend_square = start_point, end_point
        else:
            Astart_square, Aend_square = end_point, start_point

        return Astart_square, Aend_square
    else:
        print("No green arrow detected.")
        return None, None


def move_cursor_and_click(start_pos, end_pos):
    # Move the cursor and simulate click
    initial_mouse_position = pyautogui.position()
    pyautogui.moveTo(start_pos[0], start_pos[1])
    pyautogui.click()
    pyautogui.moveTo(end_pos[0], end_pos[1])
    pyautogui.click()
    pyautogui.moveTo(initial_mouse_position) 



def detect_and_convert_arrow():
        frame = capture_screen(top_left, bottom_right)
        start_point, end_point = detect_green_arrow(frame)

        if start_point and end_point:
            start_square = pixel_to_chess_square(start_point, top_left, square_size_x, square_size_y)
            end_square = pixel_to_chess_square(end_point, top_left, square_size_x, square_size_y)
            # print(f"Arrow starts at {start_square} and ends at {end_square}")

            return start_square, end_square
        
        else:
            # print("no arrow detected")
            return None




def ctrl_m(shared_state):
    start_square, end_square = detect_and_convert_arrow()
    start_pos = chess_square_to_pixel(start_square, LH_top_left, LH_square_size_x, LH_square_size_y)
    end_pos = chess_square_to_pixel(end_square, LH_top_left, LH_square_size_x, LH_square_size_y)
    ui.pause(shared_state)
    move_cursor_and_click(start_pos, end_pos)
    time.sleep(0.5)
    ui.start(shared_state)