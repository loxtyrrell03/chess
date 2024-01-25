import tkinter as tk
from PIL import Image, ImageTk, ImageDraw
import arrow_detection
from arrow_detection import detect_and_convert_arrow
from Move_checker_main import board_coordinates
import Move_checker_main
import time
from math import sin, cos, atan2, pi

lh_board_top_left = Move_checker_main.lh_board_top_left
lh_board_bottom_right = Move_checker_main.lh_board_bottom_right

def create_transparent_window(board_top_left, board_bottom_right):
    # Calculate width and height based on coordinates
    width = board_bottom_right[0] - board_top_left[0]
    height = board_bottom_right[1] - board_top_left[1]

    # Create a transparent window
    root = tk.Tk()
    root.overrideredirect(True)  # Remove window border
    root.geometry(f"{width}x{height}+{board_top_left[0]}+{board_top_left[1]}")
    root.lift()
    root.wm_attributes("-topmost", True)
    root.wm_attributes("-transparentcolor", "white")

    canvas = tk.Canvas(root, bg='white', width=width, height=height)
    canvas.pack()

    return root, canvas
from PIL import Image, ImageTk, ImageDraw
def draw_arrow(draw, start, end, arrow_color="red", width=2):
    # Draw the line
    draw.line((start, end), fill=arrow_color, width=width)
    
    # Now we will calculate and draw the arrowhead
    # Calculate the angle of the line
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    angle = atan2(dy, dx)

    # Set the length of the arrowhead
    arrowhead_length = 10

    # Calculate the points of the arrowhead
    arrow_point1 = end[0] - arrowhead_length * cos(angle - pi/6), end[1] - arrowhead_length * sin(angle - pi/6)
    arrow_point2 = end[0] - arrowhead_length * cos(angle + pi/6), end[1] - arrowhead_length * sin(angle + pi/6)

    # Draw the arrowhead
    draw.polygon([end, arrow_point1, arrow_point2], fill=arrow_color)

def update_arrow(canvas, start_coords, end_coords):
    canvas.delete("all")  # Clear the canvas

    # Ensure the canvas has been updated with its dimensions
    canvas.update_idletasks()

    # Create an image with a transparent background
    img = Image.new("RGBA", (canvas.winfo_width(), canvas.winfo_height()), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # Draw the arrow on the image
    draw_arrow(draw, start_coords, end_coords, arrow_color="red", width=2)

    # Convert the Image object to ImageTk.PhotoImage object
    canvas_image = ImageTk.PhotoImage(img)
    canvas.create_image(0, 0, anchor="nw", image=canvas_image)

    # Keep a reference to the image to prevent garbage collection
    canvas.image = canvas_image


def clear_canvas(canvas):
    canvas.delete("all")  # Clear the canvas

def get_board_coordinates(lh_board_top_left, lh_board_bottom_right):
    # Calculate the total width and height of the chessboard
    total_width = lh_board_bottom_right[0] - lh_board_top_left[0]
    total_height = lh_board_bottom_right[1] - lh_board_top_left[1]

    # Calculate the size of each square
    square_size = min(total_width, total_height) // 8

    board_coordinates = {}

    for i in range(8):  # 8 rows
        for j in range(8):  # 8 columns
            # Calculate the square's center point
            center_x = lh_board_top_left[0] + j * square_size + square_size // 2
            center_y = lh_board_top_left[1] + i * square_size + square_size // 2

            square_notation = chr(97 + j) + str(8 - i)
            board_coordinates[square_notation] = (center_x, center_y)

    return board_coordinates

board_coordinates = get_board_coordinates(lh_board_top_left, lh_board_bottom_right)

def show_arrow(board_top_left, board_bottom_right):
    root, canvas = create_transparent_window(board_top_left, board_bottom_right)
    print(board_top_left, board_bottom_right)

    while True:
        result = detect_and_convert_arrow()
        if result is not None:
            start_square, end_square = result
            
            # Access coordinates from the dictionary
            Coords_start = board_coordinates[start_square]
            Coords_end = board_coordinates[end_square]
            print(Coords_start, Coords_end )
            # Adjust the coordinates to be relative to the canvas
            canvas_start_coords = (Coords_start[0] - board_top_left[0], Coords_start[1] - board_top_left[1])
            canvas_end_coords = (Coords_end[0] - board_top_left[0], Coords_end[1] - board_top_left[1])
            update_arrow(canvas, canvas_start_coords, canvas_end_coords)
            

            
        else:
            # Clear the canvas if no arrow data is detected
            clear_canvas(canvas)

        root.update_idletasks()
        root.update()
        time.sleep(0.2)

# Example usage

show_arrow(lh_board_top_left, lh_board_bottom_right)
