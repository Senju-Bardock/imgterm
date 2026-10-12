import os
import sys

# Allows importing img_vsorx64_WLNX regardless of execution context
try:
    from dbsttinforun.dbsttingsgmepd.img_vsorx64_WLNX import show_image
except ImportError:
    try:
        from img_vsorx64_WLNX import show_image
    except ImportError:
        def show_image(name, max_width=None):
            print(f"[Image viewer module missing or image '{name}' not found]")

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

# Cross-platform keyboard detection setup for arrow navigation
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

def show_general_info():
    """Displays General game information guide."""
    clear_screen()
    print("--------------------------------")
    print("         GENERAL INFO           ")
    print("--------------------------------\n")
    print("Welcome to Dragon Ball Adventure!")
    print("- Create and customize your unique warrior.")
    print("- Choose your race, gender, and class.")
    print("- Watch out for rare mutant transformations!")
    print("- Battle, earn resources, and grow stronger.")
    input("\nPress ENTER to go back...")

def show_races_info():
    """Displays Races information guide."""
    clear_screen()
    print("--------------------------------")
    print("          RACES INFO            ")
    print("--------------------------------\n")
    print("Each race has its own unique base stats,")
    print("gender-specific attributes, passive skills,")
    print("and a rare chance to awaken as a mutant!")
    input("\nPress ENTER to go back...")

def run_game_info():
    """Main Game Info module loop using img_vsorx64_WLNX for PNG color terminal rendering."""
    current_option = 0
    options = ["General", "Races", "Go back"]
    total_options = len(options)
    
    # Flag to control that the welcome greeting only shows on the first entry per session
    show_greeting = True

    while True:
        clear_screen()
        
        # Render PNG image using custom TrueColor PNG decoder
        show_image("Senju_Bardock.png", max_width=40)
        print("------------------------------------------------------------------")
        
        # Show greeting only on the first entry
        if show_greeting:
            print("Hey, Senju Bardock here! I'm the creator of this game, and I'm here to help you.")
        
        print("What do you want to know about?\n")

        for i, option in enumerate(options):
            if i == current_option:
                print(f"--> {option}")
            else:
                print(f"    {option}")
                
        print("\n------------------------------------------------------------------")

        key = get_key()
        if key == 'UP':
            current_option = (current_option - 1) % total_options
        elif key == 'DOWN':
            current_option = (current_option + 1) % total_options
        elif key == 'ENTER':
            # Once any option is selected, disable the greeting for subsequent loops in this session
            show_greeting = False

            if current_option == 0:
                show_general_info()
            elif current_option == 1:
                show_races_info()
            elif current_option == 2:
                # Exit message before returning to main menu
                clear_screen()
                print("------------------------------------------------------------------")
                print("I'll be here if you need to know something, see you later!")
                print("------------------------------------------------------------------")
                input("\nPress ENTER to continue...")
                return