from PIL import ImageGrab
import pyautogui 
import cv2
import numpy as np


                                        #This script captures the LH and RH sides of the screen and finds the coordinates of the chessboards. 


                        #Capture LH and RH screen and get board coordinates (corners)
def rh_chessboard_coordinates():

        # Get the size of the screen
        screen_width, screen_height = pyautogui.size()

        # Calculate the coordinates for the right half of the screen
        left = screen_width // 2
        top = 200
        width = screen_width // 2  # Capture only the right half width
        height = screen_height

        # Take a screenshot of the right half of the screen
        screenshot = pyautogui.screenshot(region=(left, top, width, height))
        screenshot = np.array(screenshot)
        screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

        # Enhance contrast and apply a threshold to create a binary image
        contrast_enhanced = cv2.equalizeHist(screenshot_gray)
        _, binary_image = cv2.threshold(contrast_enhanced, 128, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Apply edge detection
        edges = cv2.Canny(screenshot_gray, threshold1=50, threshold2=200)

        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        # Assume the largest contour is the chessboard
        chessboard_contour = max(contours, key=cv2.contourArea)

        # Find the bounding rectangle for the largest contour
        x, y, w, h = cv2.boundingRect(chessboard_contour)

        # The corners of the chessboard
        top_left = (left + x, top + y)
        bottom_right = (left + x + w, top + y + h)

        # # Draw a green rectangle around the detected chessboard
        # cv2.rectangle(screenshot, (x, y), (x + w, y + h), (0, 255, 0), 2)
        # #Display the screenshot with the detected area highlighted by a green rectangle
        # cv2.imshow("Detected Chessboard", screenshot)
        # cv2.waitKey(0)
        # cv2.destroyAllWindows()   

        return top_left, bottom_right

    

def lh_chessboard_coordinates():
    try:
        # Capture the left half of the screen
        left_screen_width, left_screen_height = pyautogui.size()
        left_half_region = (0, 200, left_screen_width // 2, left_screen_height)
        left_half_screenshot = pyautogui.screenshot(region=left_half_region)
        left_half_screenshot_np = np.array(left_half_screenshot)
        left_half_screenshot_gray = cv2.cvtColor(left_half_screenshot_np, cv2.COLOR_BGR2GRAY)

        # Enhance contrast and threshold for left half
        contrast_enhanced_left_half = cv2.equalizeHist(left_half_screenshot_gray)
        _, binary_image_left_half = cv2.threshold(contrast_enhanced_left_half, 128, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Edge detection for left half
        edges_left_half = cv2.Canny(left_half_screenshot_gray, threshold1=50, threshold2=200)

        # Find contours for left half
        contours_left_half, _ = cv2.findContours(edges_left_half, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        largest_contour_left_half = max(contours_left_half, key=cv2.contourArea)

        # Bounding rectangle for the largest contour on the left half
        x_left_half, y_left_half, w_left_half, h_left_half = cv2.boundingRect(largest_contour_left_half)

        # Corners of the chessboard in the left half screenshot
        left_half_top_left = (x_left_half, y_left_half + 200)  # Adding 'top' offset
        left_half_bottom_right = (x_left_half + w_left_half, y_left_half + h_left_half + 200)  # Adding 'top' offset


        # # Draw and display the detected area on the left half screenshot
        # cv2.rectangle(left_half_screenshot_np, left_half_top_left, left_half_bottom_right, (0, 255, 0), 2)
        # cv2.imshow("Detected Chessboard on Left Half", left_half_screenshot_np)
        # cv2.waitKey(0)
        # cv2.destroyAllWindows()

        return left_half_top_left, left_half_bottom_right

    except Exception as e:
        print(f"An error occurred: {e}")
        return None, None


# print(lh_chessboard_coordinates(), rh_chessboard_coordinates())

# # Draw and display the detected area on the left half screenshot
# cv2.rectangle(left_half_screenshot_np, left_half_top_left, left_half_bottom_right, (0, 255, 0), 2)
# cv2.imshow("Detected Chessboard on Left Half", left_half_screenshot_np)
# cv2.waitKey(0)
# cv2.destroyAllWindows()