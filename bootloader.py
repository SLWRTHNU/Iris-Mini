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
GITHUB_REPO   = "Iris-Mini"
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



def gh_headers():
    h = {"User-Agent": "Pico"}
    try:
        import github_token
        token = getattr(github_token, "GITHUB_TOKEN", "")
        if token:
            h["Authorization"] = "token " + token
    except:
        pass
    return h


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
    url = RAW_BASE_URL + VERSIONS_PATH
    print("BOOTLOADER: fetching", url)
    try:
        r = requests.get(url, headers=gh_headers())
        print("BOOTLOADER: versions.json HTTP:", r.status_code)
        if r.status_code == 200:
            data = r.json()
            r.close()
            return data
        r.close()
    except Exception as e:
        print("BOOTLOADER: fetch_versions_json error:", e)
    return None



def perform_update(vers_data, lcd):
    local_v = "0.0.0"
    try:
        with open(LOCAL_VERSION_FILE, "r") as f:
            local_v = f.read().strip()
    except:
        pass

    remote_v = vers_data.get("version", "0.0.0").strip()
    if local_v == remote_v:
        return True

    draw_bottom_status(lcd, "Updating")

    files = vers_data.get("files", [])
    if not files:
        return False

    # 1) Download all files to temp files first (do NOT overwrite originals yet)
    for f_info in files:
        path = f_info.get("path")
        target = f_info.get("target", path)  # supports your "target" field
        if not path or not target:
            continue

        # For Step 3, do NOT allow bootloader overwrite while running.
        # Leave bootloader.py out of versions.json for now.
        if target == "bootloader.py":
            continue

        url = RAW_BASE_URL + path
        tmp = target + ".new"

        try:
            r = requests.get(url, headers=gh_headers())
            print("GET", path, "HTTP:", r.status_code)
            if r.status_code != 200:
                try:
                    r.close()
                except:
                    pass
                return False

            # Stream to file (fallback to r.content if raw isn't available)
            with open(tmp, "wb") as f:
                try:
                    while True:
                        chunk = r.raw.read(1024)
                        if not chunk:
                            break
                        f.write(chunk)
                except:
                    f.write(r.content)

            try:
                r.close()
            except:
                pass

            gc.collect()

        except Exception as e:
            print("Download error:", path, e)
            return False

    # 2) Swap temp files into place
    for f_info in files:
        path = f_info.get("path")
        target = f_info.get("target", path)
        if not path or not target:
            continue
        if target == "bootloader.py":
            continue

        tmp = target + ".new"
        bak = target + ".old"

        try:
            # remove old backup
            try:
                os.remove(bak)
            except:
                pass

            # backup current file if it exists
            try:
                os.rename(target, bak)
            except:
                pass

            # move new into place
            os.rename(tmp, target)

            # cleanup backup after successful swap
            try:
                os.remove(bak)
            except:
                pass

        except Exception as e:
            print("Swap error:", target, e)
            return False

    # 3) Only now mark the device as updated
    try:
        with open(LOCAL_VERSION_FILE, "w") as f:
            f.write(remote_v)
    except:
        pass

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
    print("BOOTLOADER: main() start")

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
    print("BOOTLOADER: vers_data is", "present" if vers_data else "NONE")
    if vers_data:
        perform_update(vers_data, lcd)


    run_app_main(lcd)



if __name__ == "__main__":
    main()
