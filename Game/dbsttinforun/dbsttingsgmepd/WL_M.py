import os
import sys

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

# Cross-platform keyboard detection setup
try:
    import msvcrt
    def get_key():
        key = msvcrt.getch()
        if key in (b'\x00', b'\xe0'):  # special arrow keys
            key = msvcrt.getch()
            if key == b'H': return 'UP'
            if key == b'P': return 'DOWN'
            return None
        elif key in (b'\r', b'\n'):
            return 'ENTER'
        return None
except ImportError:
    import tty
    import termios
    def get_key():
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                ch2 = sys.stdin.read(1)
                ch3 = sys.stdin.read(1)
                if ch3 == 'A': return 'UP'
                if ch3 == 'B': return 'DOWN'
            elif ch in ('\r', '\n'):
                return 'ENTER'
            return None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return None


def run_warrior_hub(username, warrior):
    """Interactive submenu when loading a warrior."""
    options = [
        "Info",
        "Travel",
        "Go Back to Menu"
    ]
    
    current_option = 0
    total_options = len(options)

    while True:
        clear_screen()
        print("--------------------------------")
        print(f"     DRAGON BALL ADVENTURE      ")
        print("--------------------------------\n")

        for i, option in enumerate(options):
            if i == current_option:
                print(f"--> {option}")
            else:
                print(f"    {option}")

        print("\n--------------------------------")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % total_options
        elif key == 'DOWN':
            current_option = (current_option + 1) % total_options
        elif key == 'ENTER':
            selected = options[current_option]

            if selected == "info":
                # In progress
                pass
            elif selected == "travel":
                # In progress
                pass
            elif selected == "Go Back to Menu":
                # Sale de la función y regresa limpiamente al bucle de M_enu_log.py
                return