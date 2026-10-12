import os
import sys
import csv
import json

# 1. Getting the absolute path of the main 'Game' folder.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Scripts subfolder to the Python path if necessary for internal imports.
SUB_DIR = os.path.join(BASE_DIR, "dbsttinforun", "dbsttingsgmepd")
if SUB_DIR not in sys.path:
    sys.path.append(SUB_DIR)

# 2. Data folder within '/Game/dbsttinforun/dbsvingsinfo/'
DATA_DIR = os.path.join(BASE_DIR, "dbsttinforun", "dbsvingsinfo")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# 3. Path to users.csv and warriors.json
USERS_FILE = os.path.join(DATA_DIR, "users.csv")
WARRIORS_FILE = os.path.join(DATA_DIR, "warriors.json")

# 4. Import the main navigation functions from the submodule modules.
from dbsttinforun.dbsttingsgmepd.M_enu_log import run_menu_log
try:
    from dbsttinforun.dbsttingsgmepd.gInfo import run_game_info
except ImportError:
    # Fallback placeholder in case gInfo.py functions aren't defined yet
    def run_game_info():
        os.system('cls' if os.name == 'nt' else 'clear')
        print("--------------------------------")
        print("           GAME INFO            ")
        print("--------------------------------\n")
        print("Game info module is under construction.")
        input("\nPress ENTER to go back...")

class CancellationException(Exception):
    """Exception raised to cancel the current flow and return to the main menu."""
    pass

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

# Controls config for cross-platform keyboard detection in menu navigation
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

def initialize_users_file():
    """Creates users.csv with headers if it does not exist."""
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(["Username", "Password"])

def read_users():
    """Reads users from users.csv and returns a dictionary {username: password}."""
    if not os.path.exists(USERS_FILE):
        return None
    
    users = {}
    with open(USERS_FILE, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            if "Username" in row and "Password" in row:
                users[row["Username"]] = row["Password"]
    return users

def save_users(users_dict):
    """Overwrites users.csv with the updated user dictionary."""
    with open(USERS_FILE, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["Username", "Password"])
        for username, password in users_dict.items():
            writer.writerow([username, password])

def delete_user_warriors(username):
    """Removes all warriors belonging to the specified username from warriors.json."""
    if not os.path.exists(WARRIORS_FILE):
        return
    
    try:
        with open(WARRIORS_FILE, 'r', encoding='utf-8') as file:
            data = json.load(file)
            warriors_list = data.get("warriors", [])
        
        # Filter out warriors that belong to the deleted user
        updated_warriors = [w for w in warriors_list if w.get("user") != username]
        
        data["warriors"] = updated_warriors

        with open(WARRIORS_FILE, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)
            
    except (json.JSONDecodeError, FileNotFoundError):
        pass

def get_input_with_cancel(prompt_text):
    """Reads user input. If left empty (just pressing ENTER), raises CancellationException."""
    try:
        user_input = input(prompt_text)
        if user_input.strip() == "":
            raise CancellationException()
        return user_input
    except (KeyboardInterrupt, EOFError):
        raise CancellationException()

def show_start_menu(selected_option):
    clear_screen()
    print("--------------------------------")
    print("     DRAGON BALL ADVENTURE      ")
    print("--------------------------------\n")
    
    # Options list updated with "Game Info" right before "Close Game"
    options = ["Sign Up", "Log In", "Delete User", "Game Info", "Close Game"]
    
    for i, option in enumerate(options):
        if i == selected_option:
            print(f"--> {option}")
        else:
            print(f"    {option}")
            
    print("\n--------------------------------")

def confirm_deletion_menu(username):
    """Submenu for confirming user deletion with arrow keys."""
    current_option = 1  # Default to 'No'
    options = ["Yes", "No"]

    while True:
        clear_screen()
        print("=== DELETE USER ===")
        print(f"Are you sure that you want to delete this user ({username})?\n")
        print("This will also delete all associated warriors!")
        print("---------------------------------------------------------------\n")

        for i, option in enumerate(options):
            if i == current_option:
                print(f"--> {option}")
            else:
                print(f"    {option}")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % len(options)
        elif key == 'DOWN':
            current_option = (current_option + 1) % len(options)
        elif key == 'ENTER':
            return options[current_option] == "Yes"

def sign_up():
    clear_screen()
    print("=== SIGN UP ===")
    print("Leave Empty and press ENTER to cancel\n")
    
    users = read_users()
    try:
        while True:
            username = get_input_with_cancel("Username: ")
            if users is not None and username in users:
                print("This user already exists\n")
                continue
            break

        password = get_input_with_cancel("Password: ")
        initialize_users_file()

        with open(USERS_FILE, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow([username, password])

        print(f"\nAccount created successfully.")
        input("\nPress ENTER to continue...")

    except CancellationException:
        return

def log_in():
    clear_screen()
    print("=== LOG IN ===")
    
    if not os.path.exists(USERS_FILE):
        print("\nNo users found, sign up to create a new user or import users")
        input("\nPress ENTER to return to the main menu...")
        return

    users = read_users()
    if not users:
        print("\nNo users found, sign up to create a new user or import users")
        input("\nPress ENTER to return to the main menu...")
        return

    print("Leave Empty and press ENTER to cancel\n")
    try:
        while True:
            username = get_input_with_cancel("Username: ")
            if username in users:
                break
            else:
                print("That user does not exist\n")

        while True:
            password = get_input_with_cancel("Password: ")
            if users[username] == password:
                print(f"\nWelcome back, {username}!")
                input("\nPress ENTER to continue...")
                # Start logged-in user flow
                run_menu_log(username)
                break
            else:
                print("Incorrect Password\n")

    except CancellationException:
        return

def delete_user():
    clear_screen()
    print("=== DELETE USER ===")
    
    if not os.path.exists(USERS_FILE):
        print("\nNo users found, sign up to create a new user or import users")
        input("\nPress ENTER to return to the main menu...")
        return

    users = read_users()
    if not users:
        print("\nNo users found, sign up to create a new user or import users")
        input("\nPress ENTER to return to the main menu...")
        return

    print("Leave Empty and press ENTER to cancel\n")
    try:
        while True:
            username = get_input_with_cancel("Username: ")
            if username in users:
                break
            else:
                print("That user does not exist\n")

        while True:
            password = get_input_with_cancel("Password: ")
            if users[username] == password:
                break
            else:
                print("Incorrect Password\n")

        confirmed = confirm_deletion_menu(username)
        if confirmed:
            # Delete user from dictionary and save users.csv
            del users[username]
            save_users(users)
            
            # Wipe out all warriors linked to this user from warriors.json
            delete_user_warriors(username)

            clear_screen()
            print("=== DELETE USER ===")
            print("\nUser and all associated warriors deleted successfully.")
            input("\nPress ENTER to continue...")
        else:
            return

    except CancellationException:
        return

def main():
    current_option = 0
    total_options = 5  # Updated total options count to include Game Info

    while True:
        show_start_menu(current_option)
        key = get_key()

        if key == 'UP':
            current_option = (current_option - 1) % total_options
        elif key == 'DOWN':
            current_option = (current_option + 1) % total_options
        elif key == 'ENTER':
            if current_option == 0:
                sign_up()
            elif current_option == 1:
                log_in()
            elif current_option == 2:
                delete_user()
            elif current_option == 3:
                # Launch Game Info module (gInfo.py)
                run_game_info()
            elif current_option == 4:
                clear_screen()
                sys.exit()

if __name__ == "__main__":
    main()