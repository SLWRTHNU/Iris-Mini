import utime as time
import machine
import network
import urequests as requests
import json
import os

GITHUB_USER   = "SLWRTHNU"
GITHUB_REPO   = "Iris-Mini"
GITHUB_BRANCH = "main"

# GitHub Contents API (works for private repos with token)
API_BASE = "https://api.github.com/repos/{}/{}/contents/".format(GITHUB_USER, GITHUB_REPO)

VERSIONS_PATH = "versions.json"
CONTROL_PATH  = "control.json"

LOCAL_VERSION_FILE = "local_version.txt"
DEVICE_ID_FILE     = "device_id.txt"
CONTROL_HASH_FILE  = "last_control_hash.txt"

_DEVICE_ID_CACHE = "" 

RAW_BASE_URL = "https://raw.githubusercontent.com/{}/{}/{}/".format(
    GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH
)
VERSIONS_URL = RAW_BASE_URL + VERSIONS_PATH



CONTROL_PATH = "control.json"
CONTROL_URL = RAW_BASE_URL + CONTROL_PATH

LOCAL_VERSION_FILE  = "local_version.txt"
DEVICE_ID_FILE = "device_id.txt"
CONTROL_HASH_FILE   = "last_control_hash.txt"

try:
    import config
    GITHUB_TOKEN = config.GITHUB_TOKEN
    print("Loaded GitHub token for OTA check.")
except Exception:
    GITHUB_TOKEN = ""
    print("Could not load config.GITHUB_TOKEN.")
    
try:
    from Pico_LCD_1_8 import LCD_1inch8 as ST7735
    LCD_AVAILABLE = True
except ImportError:
    LCD_AVAILABLE = False
    ST7735 = None

BLACK = 0x0000
WHITE = 0xFFFF


# ---------- Boot logo helpers (ADD THIS SECTION) ----------

# ---------- Boot logo helpers (FIXED SECTION) ----------

LOGO_FILE = "logo.bin"
LOGO_W = 160   # image width in pixels
LOGO_H = 128   # image height in pixels

_boot_logo_fb = None
_boot_logo_ready = False


def load_boot_logo():
    """
    Load logo.bin as an RGB565 FrameBuffer once.
    Returns the FrameBuffer object or None.
    """
    global _boot_logo_fb, _boot_logo_ready

    if _boot_logo_ready:
        return _boot_logo_fb

    try:
        with open(LOGO_FILE, "rb") as f:
            buf = f.read()
    except OSError as e:
        print("Boot logo: failed to open", LOGO_FILE, ":", e)
        _boot_logo_fb = None
        _boot_logo_ready = True
        return None # <--- Line 106 is likely here, make sure it's indented correctly.

    expected = LOGO_W * LOGO_H * 2
    if len(buf) != expected:
        print("Boot logo: size mismatch, got", len(buf), "expected", expected)
        _boot_logo_fb = None
        _boot_logo_ready = True
        return None

    try:
        import framebuf 
        fb = framebuf.FrameBuffer(bytearray(buf), LOGO_W, LOGO_H, framebuf.RGB565)
        _boot_logo_fb = fb
        _boot_logo_ready = True
        print("Boot logo loaded, bytes:", len(buf))
        return _boot_logo_fb
    except Exception as e:
        print("Boot logo: FrameBuffer init failed:", e)
        _boot_logo_fb = None
        _boot_logo_ready = True
        return None
    
    
# This function must be AFTER load_boot_logo to be called correctly
def draw_boot_logo(lcd):
    """Draws the logo once and initializes the status bar."""
    if lcd is None:
        return None
    
    lcd.fill(BLACK) # Clear screen once [cite: 3]
    
    fb = load_boot_logo() # This call now works!
    if fb is not None:
        # Blit RGB565 logo onto the LCD's framebuffer
        lcd.blit(fb, 0, 0) # Removed WHITE key argument for RGB565 blit [cite: 4]
    
    # Draw the initial status/ID
    draw_bottom_status(lcd, "Connecting") 
    print("Boot logo drawn.")

# ---------- NEW STATUS FUNCTION (REPLACES lcd_msg) ----------

# Cons# Constants for bottom bar text (assuming 8x8 font, 160x128 screen)
TEXT_HEIGHT = 8
BAR_HEIGHT = TEXT_HEIGHT + 2 # 10 pixels high for the bar
Y_POS = 128 - BAR_HEIGHT + 1 # Text Y coordinate (128 - 10 + 1 = 119)
STATUS_X = 5 # X coordinate for the status message (left alignment)

def draw_bottom_status(lcd, status_msg):
    """Draws status message (bottom left) and device ID (bottom right)."""
    if lcd is None: return

    device_id = ""
    try:
        with open(DEVICE_ID_FILE, "r") as f:
            device_id = f.read().strip()
    except Exception:
        pass 
        
    id_text = f"ID:{device_id}"
    
    # 1. Clear the entire bottom bar area to WHITE
    # Clears from Y=118 to Y=128 (10 pixels)
    # --- CHANGED: Color from BLACK to WHITE ---
    lcd.fill_rect(0, Y_POS - 1, lcd.width, BAR_HEIGHT, WHITE)

    # 2. Draw status message (bottom left)
    # --- CHANGED: Color from WHITE to BLACK ---
    lcd.text(status_msg, STATUS_X, Y_POS, BLACK) 
    
    # 3. Draw device ID (bottom right)
    ID_TEXT_X = lcd.width - (len(id_text) * 8) - 5 
    # --- CHANGED: Color from WHITE to BLACK ---
    lcd.text(id_text, ID_TEXT_X, Y_POS, BLACK) 
    
    lcd.show() # Update display

def init_lcd():
    if not LCD_AVAILABLE:
        return None
    try:
        lcd = ST7735()
        lcd.fill(BLACK)
        lcd.show()
        return lcd
    except Exception as e:
        print("LCD init failed:", e)
        return None


def lcd_msg(lcd, lines):
    """
    Show up to 4 short lines of text on the LCD.
    lines: list[str]
    """
    if lcd is None:
        return
    lcd.fill(BLACK)
    y = 20
    for line in lines[:4]:
        lcd.text(line, 5, y, WHITE)
        y += 20
    lcd.show()


# ---------------- Config helpers ----------------

DEVICE_ID_FILE = "device_id.txt"

def load_device_id():
    """
    Read a persistent device ID from device_id.txt (root of Pico filesystem).
    Returns a string or None if missing/empty.
    """
    try:
        with open(DEVICE_ID_FILE, "r") as f:
            devid = f.read().strip()
            if devid:
                return devid
    except OSError:
        pass
    return None

def _draw_device_id(lcd):
    """
    Draws the device ID in the bottom right corner of the screen.
    """
    dev_id = load_device_id()
    if not dev_id:
        dev_id = "N/A" # Fallback if ID file is missing

    # Format the message
    id_msg = "ID:{}".format(dev_id)

    # Assume 8x8 font for lcd.text() (standard MicroPython/ST7735)
    FONT_WIDTH = 8
    FONT_HEIGHT = 8
    
    # Calculate text position (bottom right corner)
    # The X position is determined by the screen width minus the length of the string
    x = lcd.width - (len(id_msg) * FONT_WIDTH)
    # The Y position is determined by the screen height minus the font height
    y = lcd.height - FONT_HEIGHT
    
    # Draw the text in BLACK (0x0000)
    # NOTE: To draw *in* black *on* the logo, you need a background color.
    # The LCD only supports one foreground color in text(). Let's use WHITE text
    # on a black background for visibility, or if the logo is colored, BLACK text on 
    # the logo's background. Since the request specified "in black" on top of the logo:
    lcd.text(id_msg, x, y, BLACK)


def load_config_wifi():
    """
    Return (ssid, password) from config.py, or (None, None) if missing.
    """
    try:
        import config
    except ImportError:
        print("config.py not found yet (first boot / AP mode).")
        return None, None

    ssid = getattr(config, "WIFI_SSID", "") or None
    pwd  = getattr(config, "WIFI_PASSWORD", "") or None

    if not ssid:
        print("No SSID")
        return None, None

    return ssid, pwd


def get_github_headers():
    """
    Headers for GitHub API requests.
    Uses GITHUB_TOKEN from config.py if present.
    """
    token = ""
    try:
        import config
        token = getattr(config, "GITHUB_TOKEN", "") or ""
    except ImportError:
        pass

    h = {
        "User-Agent": "Iris-Mini-Pico",
        "Accept": "application/vnd.github.v3.raw",
    }
    if token:
        h["Authorization"] = "token {}".format(token)
    return h

def github_api_url(path):
    # path like "app_main.py" or "lib/foo.py"
    return API_BASE + path + "?ref=" + GITHUB_BRANCH

def github_get_bytes(path):
    url = github_api_url(path)
    headers = get_github_headers()
    r = None
    try:
        r = requests.get(url, headers=headers)
        status = getattr(r, "status_code", getattr(r, "status", 0))
        if status != 200:
            try: r.close()
            except: pass
            return None, status
        data = r.content  # raw bytes because Accept: raw
        r.close()
        return data, 200
    except Exception:
        try:
            if r: r.close()
        except:
            pass
        return None, 0

def github_get_json(path):
    b, status = github_get_bytes(path)
    if status != 200 or not b:
        return None, status
    try:
        if isinstance(b, bytes):
            b = b.decode("utf-8")
        return json.loads(b), 200
    except Exception:
        return None, status


def draw_logo_with_status(lcd, status_text):
    """
    Draw the logo full-screen and overlay a small status in the bottom-left.
    Text is drawn in white on a small black strip.
    """
    if lcd is None:
        return

    buf = load_boot_logo()
    try:
        # Draw logo if available
        if buf is not None:
            fb = framebuf.FrameBuffer(buf, BOOT_LOGO_WIDTH, BOOT_LOGO_HEIGHT, framebuf.RGB565)
            lcd.blit(fb, 0, 0)
        else:
            # Fallback: just clear screen if no logo
            lcd.fill(BLACK)

        # Draw a black strip at the bottom for the status text
        # (FrameBuffer API provides fill_rect on the LCD object)
        try:
            lcd.fill_rect(0, BOOT_LOGO_HEIGHT - 16, BOOT_LOGO_WIDTH, 16, BLACK)
        except AttributeError:
            # Very old firmware fallback, in case fill_rect is missing
            for y in range(BOOT_LOGO_HEIGHT - 16, BOOT_LOGO_HEIGHT):
                for x in range(BOOT_LOGO_WIDTH):
                    lcd.pixel(x, y, BLACK)

        # Status text in bottom-left
        lcd.text(status_text, 2, BOOT_LOGO_HEIGHT - 14, WHITE)
        lcd.show()
    except Exception as e:
        print("Logo Error", e)


# ---------------- Wi-Fi ----------------
def connect_wifi(lcd, ssid, pwd, timeout_sec=10):
    """
    Connect to Wi-Fi.
    Returns True on success, False on failure.
    """
    if ssid is None or pwd is None:
        print("Config Failed")
        return False



    ap = network.WLAN(network.AP_IF)
    ap.active(False)

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect(ssid, pwd)

    t0 = time.ticks_ms()
    while not sta.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_sec * 1000:
            print("Wi-Fi connect timeout")
            draw_bottom_status(lcd, "Wi-Fi failed: Timeout") # MODIFIED
            return False
        time.sleep(0)

    ip = sta.ifconfig()[0]

    return True


# ---------------- Version helpers ----------------

def load_local_version():
    try:
        with open(LOCAL_VERSION_FILE, "r") as f:
            v = f.read().strip()
            if v:
                return v
    except OSError:
        pass
    return "0.0.0"


def save_local_version(version_str):
    try:
        with open(LOCAL_VERSION_FILE, "w") as f:
            f.write(version_str)
    except OSError as e:
        print("Failed to write local version file:", e)

# ... (Place this block where fetch_versions_json is currently defined)
def fetch_versions_json():
    print("Attempting to fetch:", github_api_url(VERSIONS_PATH))
    data, status = github_get_json(VERSIONS_PATH)
    print("Fetch status:", status)
    return data if status == 200 else None


def ensure_dirs_for(target_path):
    """
    Create any intermediate directories for target_path if needed.
    E.g. "lib/foo/bar.py" -> create "lib", then "lib/foo".
    """
    parts = target_path.split("/")
    if len(parts) <= 1:
        return

    path = ""
    for p in parts[:-1]:
        if not p:
            continue
        path = (path + "/" + p) if path else p
        try:
            os.mkdir(path)
        except OSError:
            pass

def download_file(remote_path, target_path, lcd):
    """
    Download one file from GitHub (raw) and write it to target_path.
    Stream to disk in chunks to avoid RAM blowups on large files.
    Returns True/False for success.
    """
    url = RAW_BASE_URL + remote_path
    headers = get_github_headers()

    print("Updating", target_path, "from", url)
    lcd_msg(lcd, ["Updating", target_path])

    r = None
    try:
        r = requests.get(url, headers=headers)

        status = getattr(r, "status_code", getattr(r, "status", 0))
        if status != 200:
            print("Download failed with status", status)
            try:
                r.close()
            except Exception:
                pass
            lcd_msg(lcd, ["DL failed", target_path])
            time.sleep(1)
            return False

        ensure_dirs_for(target_path)

        # Stream the body to file in small chunks
        with open(target_path, "wb") as f:
            while True:
                chunk = r.raw.read(1024)
                if not chunk:
                    break
                f.write(chunk)

        try:
            r.close()
        except Exception:
            pass

        print("Saved", target_path, "OK")
        return True

    except Exception as e:
        print("Exception downloading", remote_path, "->", target_path, ":", e)
        try:
            if r:
                r.close()
        except Exception:
            pass
        lcd_msg(lcd, ["DL error", target_path])
        time.sleep(1)
        return False



def perform_update(vers_data, lcd):
    """
    Check for new files and download them if needed.
    Returns True if update check/process finished successfully, False otherwise.
    """
    local_version = load_local_version()
    remote_version = vers_data.get("version", "0.0.0")

    # No update needed
    if remote_version == local_version:

        return True

    # We DO need to update
    files = vers_data.get("files", [])
    if not isinstance(files, list) or not files:
        print("versions.json has no files[] list.")
        return False

    draw_bottom_status(lcd, f"Updating") # MODIFIED
    print("Updating")

    for entry in files:
        try:
            remote_path = entry["path"]
            target_path = entry.get("target", remote_path)
        except Exception:
            print("Bad entry in files[]:", entry)
            return False
        
        # Update status bar for each file download
        draw_bottom_status(lcd, f"Updating")

        ok = download_file(remote_path, target_path, lcd)
        if not ok:
            print("Aborting update due to failure on", target_path)
            return False

    # All files downloaded OK: store version and HARD RESET
    save_local_version(remote_version)
    draw_bottom_status(lcd, f"Firmware v{remote_version} Update Complete") # MODIFIED
    time.sleep(0)

    print("Updating")
    machine.reset() # We never return from here
    

def get_or_create_device_id():
    """
    Persistent DEVICE_ID stored in device_id.txt.
    Will NOT overwrite a manually assigned ID (ex: 0000, 1234).
    Auto-generates only if missing.
    """
    global _DEVICE_ID_CACHE
    try:
        with open(DEVICE_ID_FILE, "r") as f:
            dev_id = f.read().strip()
            if dev_id:
                return dev_id
    except OSError:
        pass  # File missing

    # File missing → auto generate one backup ID
    try:
        import ubinascii
        raw = machine.unique_id()
        hexid = ubinascii.hexlify(raw).decode().upper()
        dev_id = hexid[:4]  # 4 hex chars (fallback only)
    except Exception:
        dev_id = "0000"     # worst-case fallback

    try:
        with open(DEVICE_ID_FILE, "w") as f:
            f.write(dev_id)
        print("Created new DEVICE_ID:", dev_id)
    except OSError as e:
        print("Failed to write DEVICE_ID_FILE:", e)

    return dev_id

def _simple_hash(s):
    """
    Tiny string hash so the device can detect changes in control.json.
    """
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h


def _load_last_control_hash():
    try:
        with open(CONTROL_HASH_FILE, "r") as f:
            txt = f.read().strip()
            if txt:
                return int(txt)
    except OSError:
        pass
    return None


def _save_last_control_hash(h):
    try:
        with open(CONTROL_HASH_FILE, "w") as f:
            f.write(str(h))
    except OSError as e:
        print("Failed to write CONTROL_HASH_FILE:", e)


def check_remote_commands():
    """
    Check control.json on GitHub for commands targeting this device.
    If this device is listed, perform reboot or force-update.
    """
    device_id = get_or_create_device_id()
    if not device_id:
        print("check_remote_commands: no device_id")
        return

    print("Checking remote commands for", device_id)

    try:
        data, status = github_get_json(CONTROL_PATH)
        print("control.json status:", status)
        if status != 200 or data is None:
            return

        reboot_ids = data.get("reboot_ids", [])
        force_update_ids = data.get("force_update_ids", [])

        if device_id in reboot_ids:
            print("Remote reboot for", device_id)
            machine.reset()

        if device_id in force_update_ids:
            print("Remote force-update for", device_id)
            try:
                os.remove(LOCAL_VERSION_FILE)
            except OSError:
                pass
            machine.reset()

    except Exception as e:
        print("Error fetching/parsing control.json:", e)
        return




# ---------------- main/runner ----------------

def run_app_main():
    """
    Import and execute app_main.main().
    """
    try:
        import app_main as app
    except ImportError as e:
        print("ERROR: app_main.py not found or bad:", e)
        return

    if hasattr(app, "main"):
        try:
            app.main()
        except Exception as e:
            print("Error running app.main():", e)
    else:
        print("app_main.py has no main() function; nothing to call.")
def main():
    lcd = init_lcd()

    # 1. Draw logo ONCE at the start
    draw_boot_logo(lcd) # NEW

    # Get local version for status message (optional)
    local_version = load_local_version()
    


    ssid, pwd = load_config_wifi()

    # If no Wi-Fi config yet, skip OTA and let app_main handle AP config
    if ssid is None or pwd is None:
        print("No Wi-Fi config; skipping OTA and running app_main directly")
        draw_bottom_status(lcd, "No Wi-Fi config. Skipping OTA.") # MODIFIED
        time.sleep(0)
        run_app_main()
        return

    # Try Wi-Fi for OTA
    draw_bottom_status(lcd, "Connecting") # <--- ADD STATUS
    if not connect_wifi(lcd, ssid, pwd):
        print("Wi-Fi failed; skipping OTA and running app_main.py")
        # connect_wifi already draws an error status
        run_app_main()
        return

    
    vers_data = fetch_versions_json()
    if vers_data is None:
        print("Could not fetch versions.json; running app_main.py anyway.")
        draw_bottom_status(lcd, "Connecting") # MODIFIED
        time.sleep(0)
        run_app_main()
        return

    ok = perform_update(vers_data, lcd)
    if not ok:
        print("Update failed; running app_main.py anyway.")
        # perform_update already draws an error status
        time.sleep(0)
        run_app_main()
        return
    
    # Run app_main if update succeeded but didn't trigger a reset (though it should)
    run_app_main()
    

if __name__ == "__main__":
    main()



