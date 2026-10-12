import os
import sys
import json
import random

# Allows the import of WL_M regardless of where the script is executed.
try:
    from dbsttinforun.dbsttingsgmepd import WL_M
except ImportError:
    import WL_M

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

# Cross-platform keyboard detection setup
try:
    import msvcrt
    def get_key():
        key = msvcrt.getch()
        if key in (b'\x00', b'\xe0'):
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

# Base directory definitions
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DBSTTINFORUN_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SAVES_DIR = os.path.join(DBSTTINFORUN_DIR, "dbsvingsinfo")

# Files inside dbsvingsinfo
WARRIORS_FILE = os.path.join(SAVES_DIR, "warriors.json")
RACES_FILE = os.path.join(SAVES_DIR, "races.json")
BASIC_SKILLS_FILE = os.path.join(SAVES_DIR, "basic_skills.json")


def ensure_warriors_file_exists():
    """Checks if warriors.json exists in dbsvingsinfo. Creates it with the root 'warriors' list if missing or invalid."""
    if not os.path.exists(SAVES_DIR):
        os.makedirs(SAVES_DIR)
        
    if not os.path.exists(WARRIORS_FILE):
        with open(WARRIORS_FILE, 'w', encoding='utf-8') as file:
            json.dump({"warriors": []}, file, indent=4)


def load_races_data():
    """Reads and returns the complete list of race objects from races.json."""
    if os.path.exists(RACES_FILE):
        with open(RACES_FILE, 'r', encoding='utf-8') as file:
            data = json.load(file)
            return data.get("races", [])
    return []


def load_basic_skills_data():
    """Reads and returns basic skills dictionary from basic_skills.json."""
    if os.path.exists(BASIC_SKILLS_FILE):
        with open(BASIC_SKILLS_FILE, 'r', encoding='utf-8') as file:
            return json.load(file)
    return {}


def load_user_warriors(username):
    """Loads and filters warriors belonging ONLY to the currently logged in user."""
    ensure_warriors_file_exists()
    try:
        with open(WARRIORS_FILE, 'r', encoding='utf-8') as file:
            data = json.load(file)
            warriors_list = data.get("warriors", [])
            return [w for w in warriors_list if w.get("user") == username]
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def load_warrior_flow(username):
    """Handles the selection and loading of an existing warrior for the user."""
    user_warriors = load_user_warriors(username)

    if not user_warriors:
        clear_screen()
        print("--------------------------------")
        print("          LOAD WARRIOR          ")
        print("--------------------------------\n")
        print(f"No warriors found for user '{username}'.")
        print("Please create a new warrior first!")
        input("\nPress ENTER to go back...")
        return

    warrior_names = [f"{w['warrior_name']} (Lvl {w.get('level', 1)} - {w.get('race', 'Unknown')})" for w in user_warriors]
    
    current_option = 0
    total_options = len(warrior_names) + 1
    cancel_index = len(warrior_names)

    while True:
        clear_screen()
        print("----------------------------------")
        print(" With who do you want to play as? ")
        print("----------------------------------\n")

        for i, name in enumerate(warrior_names):
            if i == current_option:
                print(f"--> {name}")
            else:
                print(f"    {name}")

        print("\n--------------------------------")
        if current_option == cancel_index:
            print("--> Cancel")
        else:
            print("    Cancel")
        print("--------------------------------")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % total_options
        elif key == 'DOWN':
            current_option = (current_option + 1) % total_options
        elif key == 'ENTER':
            if current_option == cancel_index:
                return
            
            selected_warrior = user_warriors[current_option]
            WL_M.run_warrior_hub(username, selected_warrior)
            return


def confirm_deletion_menu(warrior_name):
    """Asks confirmation to delete the warrior with Yes / No navigable options."""
    options = ["Yes", "No"]
    current_option = 1  # Default on 'No' for safety

    while True:
        clear_screen()
        print("---------------------------------------------------------------")
        print("                 DELETE WARRIOR CONFIRMATION                   ")
        print("---------------------------------------------------------------")
        print(f" Are you sure that you want to delete {warrior_name}?")
        print(" You will lose everything you built.")
        print("---------------------------------------------------------------\n")

        for i, option in enumerate(options):
            if i == current_option:
                print(f"--> {option}")
            else:
                print(f"    {option}")

        print("\n---------------------------------------------------------------")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % len(options)
        elif key == 'DOWN':
            current_option = (current_option + 1) % len(options)
        elif key == 'ENTER':
            return options[current_option] == "Yes"


def delete_warrior_from_json(username, warrior_name):
    """Permanently removes the chosen warrior from warriors.json."""
    ensure_warriors_file_exists()
    try:
        with open(WARRIORS_FILE, 'r', encoding='utf-8') as file:
            data = json.load(file)
            warriors_list = data.get("warriors", [])

        updated_warriors = [
            w for w in warriors_list 
            if not (w.get("user") == username and w.get("warrior_name") == warrior_name)
        ]

        data["warriors"] = updated_warriors

        with open(WARRIORS_FILE, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)
            
    except (json.JSONDecodeError, FileNotFoundError):
        pass


def delete_warrior_flow(username):
    """Handles the selection and deletion of an existing warrior."""
    user_warriors = load_user_warriors(username)

    if not user_warriors:
        clear_screen()
        print("--------------------------------")
        print("         DELETE WARRIOR         ")
        print("--------------------------------\n")
        print(f"No warriors found for user '{username}'.")
        print("There is no warrior to delete!")
        input("\nPress ENTER to go back...")
        return

    warrior_names = [f"{w['warrior_name']} (Lvl {w.get('level', 1)} - {w.get('race', 'Unknown')})" for w in user_warriors]
    
    current_option = 0
    total_options = len(warrior_names) + 1
    cancel_index = len(warrior_names)

    while True:
        clear_screen()
        print("--------------------------------------")
        print(" Which warrior do you want to delete? ")
        print("--------------------------------------\n")

        for i, name in enumerate(warrior_names):
            if i == current_option:
                print(f"--> {name}")
            else:
                print(f"    {name}")

        print("\n--------------------------------")
        if current_option == cancel_index:
            print("--> Cancel")
        else:
            print("    Cancel")
        print("--------------------------------")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % total_options
        elif key == 'DOWN':
            current_option = (current_option + 1) % total_options
        elif key == 'ENTER':
            if current_option == cancel_index:
                return
            
            target_warrior = user_warriors[current_option]
            warrior_name = target_warrior['warrior_name']

            confirmed = confirm_deletion_menu(warrior_name)
            
            if confirmed:
                delete_warrior_from_json(username, warrior_name)
                clear_screen()
                print("--------------------------------")
                print("        WARRIOR DELETED         ")
                print("--------------------------------\n")
                print(f"Warrior '{warrior_name}' has been deleted successfully.")
                input("\nPress ENTER to continue...")
            
            return


def select_race_menu():
    """Displays an arrow-navigable menu to choose a race, with a separate Cancel option."""
    races_data = load_races_data()
    if not races_data:
        clear_screen()
        print("races.json file not found or is empty.")
        input("\nPress ENTER to go back...")
        return None

    races_names = [race["name"] for race in races_data]
    
    current_option = 0
    total_options = len(races_names) + 1
    cancel_index = len(races_names)

    while True:
        clear_screen()
        print("--------------------------------")
        print("        Choose your race:       ")
        print("--------------------------------\n")

        for i, race_name in enumerate(races_names):
            if i == current_option:
                print(f"--> {race_name}")
            else:
                print(f"    {race_name}")

        print("\n--------------------------------")
        if current_option == cancel_index:
            print("--> Cancel")
        else:
            print("    Cancel")
        print("--------------------------------")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % total_options
        elif key == 'DOWN':
            current_option = (current_option + 1) % total_options
        elif key == 'ENTER':
            if current_option == cancel_index:
                return None
            return races_data[current_option]


def select_gender_menu():
    """Displays an arrow-navigable menu to choose gender if the race supports it."""
    genders = ["Male", "Female"]
    current_option = 0

    while True:
        clear_screen()
        print("--------------------------------")
        print("       Choose your gender:      ")
        print("--------------------------------\n")

        for i, gender in enumerate(genders):
            if i == current_option:
                print(f"--> {gender}")
            else:
                print(f"    {gender}")

        print("\n--------------------------------")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % len(genders)
        elif key == 'DOWN':
            current_option = (current_option + 1) % len(genders)
        elif key == 'ENTER':
            return genders[current_option].lower()  # returns "male" or "female"


def select_class_menu():
    """Displays an arrow-navigable menu to choose the archetype class ('Who are you?')."""
    classes = [
        "Hand-to-Hand fighter",
        "Ki fighter",
        "Wizard",
        "Hand-to-Hand Ki fighter",
        "Hand-to-Hand Wizard",
        "Ki wizard"
    ]

    current_option = 0
    total_options = len(classes) + 1
    cancel_index = len(classes)

    while True:
        clear_screen()
        print("--------------------------------")
        print("          Who are you?          ")
        print("--------------------------------\n")

        for i, class_name in enumerate(classes):
            if i == current_option:
                print(f"--> {class_name}")
            else:
                print(f"    {class_name}")

        print("\n--------------------------------")
        if current_option == cancel_index:
            print("--> Cancel")
        else:
            print("    Cancel")
        print("--------------------------------")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % total_options
        elif key == 'DOWN':
            current_option = (current_option + 1) % total_options
        elif key == 'ENTER':
            if current_option == cancel_index:
                return None
            return classes[current_option]


def generate_initial_skills(fighter_class):
    """Filters basic_skills.json and returns only skill names according to class rules."""
    basic_data = load_basic_skills_data()
    all_specials = basic_data.get("basic_skills_for_every_CAC", [])
    all_ultimates = basic_data.get("basic_ultimate_skills_for_every_CAC", [])

    specials_by_type = {"Attack": [], "Energy": [], "Magic": []}
    for sk in all_specials:
        if sk["type"] in specials_by_type:
            specials_by_type[sk["type"]].append(sk["name"])

    ultimates_by_type = {"Attack": [], "Energy": [], "Magic": []}
    for ult in all_ultimates:
        if ult["type"] in ultimates_by_type:
            ultimates_by_type[ult["type"]].append(ult["name"])

    chosen_specials = []
    chosen_ultimate = None

    if fighter_class == "Hand-to-Hand fighter":
        chosen_specials = specials_by_type["Attack"][:2]
        chosen_ultimate = random.choice(ultimates_by_type["Attack"])

    elif fighter_class == "Ki fighter":
        chosen_specials = specials_by_type["Energy"][:2]
        chosen_ultimate = random.choice(ultimates_by_type["Energy"])

    elif fighter_class == "Wizard":
        chosen_specials = specials_by_type["Magic"][:2]
        chosen_ultimate = random.choice(ultimates_by_type["Magic"])

    elif fighter_class == "Hand-to-Hand Ki fighter":
        if specials_by_type["Attack"]: chosen_specials.append(specials_by_type["Attack"][0])
        if specials_by_type["Energy"]: chosen_specials.append(specials_by_type["Energy"][0])
        pool = ultimates_by_type["Attack"] + ultimates_by_type["Energy"]
        chosen_ultimate = random.choice(pool)

    elif fighter_class == "Hand-to-Hand Wizard":
        if specials_by_type["Attack"]: chosen_specials.append(specials_by_type["Attack"][0])
        if specials_by_type["Magic"]: chosen_specials.append(specials_by_type["Magic"][0])
        pool = ultimates_by_type["Attack"] + ultimates_by_type["Magic"]
        chosen_ultimate = random.choice(pool)

    elif fighter_class == "Ki wizard":
        if specials_by_type["Energy"]: chosen_specials.append(specials_by_type["Energy"][0])
        if specials_by_type["Magic"]: chosen_specials.append(specials_by_type["Magic"][0])
        pool = ultimates_by_type["Energy"] + ultimates_by_type["Magic"]
        chosen_ultimate = random.choice(pool)

    special_slots = chosen_specials + [None] * (4 - len(chosen_specials))
    ultimate_slots = [chosen_ultimate, None] if chosen_ultimate else [None, None]

    return special_slots, ultimate_slots


def check_mutant_status(race_info):
    """
    Evaluates in silence if the warrior becomes a mutant based on race_info['mutation'].
    Returns a tuple: (mutant_status: str, passive_skill_name: str, mutant_exp_boost: str or None)
    """
    mutation_data = race_info.get("mutation", [])
    
    is_mutant = "No"
    passive_skill = race_info.get("passive_skill", "")
    mutant_exp_boost = None

    if not mutation_data:
        return is_mutant, passive_skill, mutant_exp_boost

    info = mutation_data[0]
    posibility = info.get("posibility", "No")

    if posibility == "Yes":
        prob_str = info.get("probability", "0%").replace("%", "").strip()
        try:
            probability = float(prob_str)
        except ValueError:
            probability = 0.0

        roll = random.uniform(0, 100)
        
        if roll < probability:
            is_mutant = "Yes"
            benefits = info.get("mutation_beneficts", [])
            if benefits:
                passive_skill = benefits[0].get("passive_skill", passive_skill)
                mutant_exp_boost = benefits[0].get("EXP Boost", None)

    return is_mutant, passive_skill, mutant_exp_boost


def save_warrior(username, warrior_name, race_info, fighter_class, gender):
    """Saves a new warrior dynamically into warriors.json with gender stats, stat RNG (-15% to +15%), exp boost, and mutant check."""
    ensure_warriors_file_exists()

    try:
        with open(WARRIORS_FILE, 'r', encoding='utf-8') as file:
            data = json.load(file)
            if not isinstance(data, dict) or "warriors" not in data:
                data = {"warriors": []}
    except (json.JSONDecodeError, FileNotFoundError):
        data = {"warriors": []}

    special_slots, ultimate_slots = generate_initial_skills(fighter_class)
    
    # Check mutant status and potential mutant EXP boost
    mutant_status, chosen_passive, mutant_exp = check_mutant_status(race_info)

    # Determine base stats based on gender presence
    if "sex" in race_info and gender:
        sex_list = race_info["sex"]
        if sex_list and gender in sex_list[0]:
            stats = sex_list[0][gender][0]
        else:
            stats = sex_list[0]["male"][0]  # Fallback
    else:
        stats = race_info  # For races without gender definition (Namekian, Bio-Android, etc.)

    # Apply RNG variation (-15% to +15%) independently to each stat with rounding
    def roll_stat(base_value):
        multiplier = random.uniform(0.85, 1.15)
        return round(base_value * multiplier)

    hp_final = roll_stat(stats["HP"])
    ki_final = roll_stat(stats["Ki"])
    attack_final = roll_stat(stats["Attack"])
    defense_final = roll_stat(stats["Defense"])
    speed_final = roll_stat(stats["Speed"])
    special_final = roll_stat(stats["Special"])
    magic_final = roll_stat(stats["Magic"])

    # Determine EXP Boost (Mutant EXP boost takes precedence if available, otherwise race default, otherwise "x1")
    exp_boost = mutant_exp if mutant_exp else race_info.get("EXP Boost", "x1")

    new_warrior = {
        "user": username,
        "warrior_name": warrior_name,
        "race": race_info["name"],
        "sex": gender,
        "class": fighter_class,
        "mutant": mutant_status,
        "level": 1,
        "EXP_Boost": exp_boost,
        "allies": [None, None, None, None, None],
        "romantic_partner": None,
        "MAX_HP": hp_final,
        "MAX_Ki": ki_final,
        "HP": hp_final,
        "Ki": ki_final,
        "Attack": attack_final,
        "Defense": defense_final,
        "Speed": speed_final,
        "Special": special_final,
        "Magic": magic_final,
        "passive_skill": chosen_passive,
        "special_skills": special_slots,
        "ultimate_skills": ultimate_slots,
        "transformations": [],                  
        "active_transformations": [None, None, None, None],  
        "items": [],                            
        "resources": {
            "satan_medals": 0,
            "katchin_fragments": 0,
            "stardust": 0
        }
    }

    data["warriors"].append(new_warrior)

    with open(WARRIORS_FILE, 'w', encoding='utf-8') as file:
        json.dump(data, file, indent=4)

    return mutant_status


def new_warrior_flow(username):
    """Workflow for creating and saving a New Warrior."""
    ensure_warriors_file_exists()

    clear_screen()
    print("--------------------------------")
    print("      NEW WARRIOR CREATION      ")
    print("--------------------------------")
    print("Leave Empty and press ENTER to cancel\n")
    
    warrior_name = input("Enter your warrior's name: ").strip()

    if not warrior_name:
        return

    selected_race_info = select_race_menu()
    if not selected_race_info:
        return

    # Check if race has 'sex' attribute to prompt gender selection BEFORE 'Who are you?'
    gender = None
    if "sex" in selected_race_info:
        gender = select_gender_menu()

    selected_class = select_class_menu()
    if not selected_class:
        return

    # Save warrior with randomized stats, gender, and EXP boost
    mutant_status = save_warrior(username, warrior_name, selected_race_info, selected_class, gender)

    clear_screen()
    print("--------------------------------")
    print("        WARRIOR CREATED!        ")
    print("--------------------------------\n")
    print(f"Name: {warrior_name}")
    print(f"Race: {selected_race_info['name']}")
    if gender:
        print(f"Gender: {gender.capitalize()}")
    print(f"Class: {selected_class}")
    print(f"Level: 1")

    if mutant_status == "Yes":
        print("\n★------------------------------★")
        print("      YOU GOT A MUTANT!         ")
        print("★------------------------------★")

    print("\nWarrior saved successfully!")
    input("\nPress ENTER to continue...")


def show_logged_menu(username, selected_option):
    clear_screen()
    print("--------------------------------")
    print(f"     DRAGON BALL ADVENTURE      ")
    print("--------------------------------\n")
    
    options = ["New Warrior", "Load Warrior", "Delete Warrior", "Log out"]
    
    for i, option in enumerate(options):
        if i == selected_option:
            print(f"--> {option}")
        else:
            print(f"    {option}")
            
    print("\n--------------------------------")


def run_menu_log(username):
    current_option = 0
    total_options = 4

    while True:
        show_logged_menu(username, current_option)
        key = get_key()

        if key == 'UP':
            current_option = (current_option - 1) % total_options
        elif key == 'DOWN':
            current_option = (current_option + 1) % total_options
        elif key == 'ENTER':
            if current_option == 0:
                new_warrior_flow(username)
            elif current_option == 1:
                load_warrior_flow(username)
            elif current_option == 2:
                delete_warrior_flow(username)
            elif current_option == 3:
                break


if __name__ == "__main__":
    run_menu_log("Guest")