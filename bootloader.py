# bootloader.py (Iris Classic 1.8")
import utime as time
import machine
import network
import urequests as requests
import json
import os
import gc

# ---------- GitHub & Paths ----------
GITHUB_USER   = "SLWRTHNU"
GITHUB_REPO   = "Iris-Classic"
GITHUB_BRANCH = "main"
RAW_BASE_URL  = "https://raw.githubusercontent.com/{}/{}/{}/".format(GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH)
API_BASE      = "https://api.github.com/repos/{}/{}/contents/".format(GITHUB_USER, GITHUB_REPO)

VERSIONS_PATH = "versions.json"
CONTROL_PATH  = "control.json"
LOCAL_VERSION_FILE = "local_version.txt"
DEVICE_ID_FILE     = "device_id.txt"
CONTROL_HASH_FILE  = "last_control_hash.txt"

# ---------- Display driver (1.8") ----------
try:
    from Pico_LCD_1_8 import LCD_1inch8 as LCD_Driver
except ImportError:
    try:
        from ST7735 import ST7735 as LCD_Driver
    except ImportError:
        # If no driver is found, we continue but LCD functions will be nullified
        LCD_Driver = None

BLACK = 0x0000
WHITE = 0xFFFF

# ---------- Boot logo constants (1.8") ----------
LOGO_FILE   = "logo.bin"
LOGO_W      = 160
LOGO_H      = 128
TEXT_HEIGHT = 8
BAR_HEIGHT  = TEXT_HEIGHT + 1
# Positioning the white status bar at the bottom of 128px height
Y_POS       = 128 - BAR_HEIGHT + 1
STATUS_X    = 5

# ---------- LCD Logic ----------

def init_lcd():
    if LCD_Driver is None:
        return None
    try:
        lcd = LCD_Driver()
        # Compatibility shim for .show() vs .show_up()
        if not hasattr(lcd, "show") and hasattr(lcd, "show_up"):
            lcd.show = lcd.show_up
        
        lcd.fill(BLACK)
        lcd.show()
        return lcd
    except Exception as e:
        print("LCD init failed:", e)
        return None

def draw_bottom_status(lcd, status_msg):
    if lcd is None: return
    
    # Get ID for the right side
    device_id = "N/A"
    try:
        if os.stat(DEVICE_ID_FILE):
            with open(DEVICE_ID_FILE, "r") as f:
                device_id = f.read().strip()
    except Exception: pass

    id_text = "ID:{}".format(device_id)

    # Draw the white background bar
    lcd.fill_rect(0, Y_POS - 1, lcd.width, BAR_HEIGHT, WHITE)
    # Draw Left Status
    lcd.text(status_msg, STATUS_X, Y_POS, BLACK)
    # Draw Right ID
    id_x = lcd.width - (len(id_text) * 8) - 5
    lcd.text(id_text, id_x, Y_POS, BLACK)
    lcd.show()

def draw_boot_logo(lcd):
    if lcd is None: return
    # 1.8" expectation: 160 * 128 * 2 bytes = 40,960 bytes
    expected = LOGO_W * LOGO_H * 2
    
    try:
        st = os.stat(LOGO_FILE)
        if st[6] != expected:
            print("Logo size mismatch. Expected", expected)
            lcd.fill(BLACK)
        else:
            with open(LOGO_FILE, "rb") as f:
                f.readinto(lcd.buffer)
        lcd.show()
    except Exception as e:
        print("Logo error:", e)
        lcd.fill(BLACK)
        lcd.show()

    draw_bottom_status(lcd, "Connecting")

def status_error(lcd, code):
    msg = "ERR:{:03d}".format(code)
    draw_bottom_status(lcd, msg)
    time.sleep(2)

# ---------- Wifi & Updates ----------

def load_config_wifi():
    try:
        import config
        return getattr(config, "WIFI_SSID", None), getattr(config, "WIFI_PASSWORD", None)
    except ImportError:
        return None, None

def connect_wifi(lcd, ssid, pwd, timeout_sec=10):
    if not ssid: return False
    draw_bottom_status(lcd, "Connecting")
    
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect(ssid, pwd)

    t0 = time.ticks_ms()
    while not sta.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_sec * 1000:
            return False
        time.sleep_ms(100)
    return True

def fetch_versions_json(lcd):
    try:
        # Use headers if token exists in config
        headers = {"User-Agent": "Pico"}
        try:
            import config
            token = getattr(config, "GITHUB_TOKEN", "")
            if token: headers["Authorization"] = "token " + token
        except: pass

        r = requests.get(RAW_BASE_URL + VERSIONS_PATH, headers=headers)
        if r.status_code == 200:
            data = r.json()
            r.close()
            return data
        r.close()
    except: pass
    return None

def perform_update(vers_data, lcd):
    local_v = "0.0.0"
    try:
        with open(LOCAL_VERSION_FILE, "r") as f: local_v = f.read().strip()
    except: pass

    remote_v = vers_data.get("version", "0.0.0")
    if local_v == remote_v:
        return True

    draw_bottom_status(lcd, "Updating")
    files = vers_data.get("files", [])
    for f_info in files:
        path = f_info["path"]
        try:
            r = requests.get(RAW_BASE_URL + path)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
            r.close()
        except: return False

    with open(LOCAL_VERSION_FILE, "w") as f: f.write(remote_v)
    machine.reset()

# ---------- Runner ----------

def run_app_main(lcd=None):
    gc.collect()
    try:
        import app_main
        app_main.main(lcd)
    except Exception as e:
        print("App failed:", e)

def main():
    lcd = init_lcd()
    draw_boot_logo(lcd)

    ssid, pwd = load_config_wifi()
    if not ssid:
        status_error(lcd, 20)
        run_app_main(lcd)
        return

    if not connect_wifi(lcd, ssid, pwd):
        status_error(lcd, 0)
        run_app_main(lcd)
        return

    vers_data = fetch_versions_json(lcd)
    if vers_data:
        perform_update(vers_data, lcd)

    # Handover to app_main without clearing lcd
    gc.collect()
    run_app_main(lcd)

if __name__ == "__main__":
    main()
