# bootloader.py (Iris Classic 1.8")
import utime as time
import machine
import network
import urequests as requests
import json
import os
import gc
import ubinascii


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

def _get_token():
    try:
        import github_token
        return getattr(github_token, "GITHUB_TOKEN", "")
    except:
        return ""

def gh_api_headers_json():
    h = {
        "User-Agent": "Pico",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _get_token()
    if token:
        h["Authorization"] = "Bearer " + token
    return h

def gh_api_headers_raw():
    h = {
        "User-Agent": "Pico",
        "Accept": "application/vnd.github.v3.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _get_token()
    if token:
        h["Authorization"] = "Bearer " + token
    return h



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
BAR_HEIGHT  = TEXT_HEIGHT + 2
# Positioning the white status bar at the bottom of 128px height
Y_POS       = 128 - BAR_HEIGHT + 1
STATUS_X    = 5








# ---------- LCD Logic ----------

def draw_bottom_status(lcd, status_msg, show_id=None):
    if lcd is None:
        return

    # Auto behavior:
    # - Show ID only for Connecting / Loading
    # - Also show ID on ERR codes
    if show_id is None:
        show_id = (
            status_msg.startswith("Connecting")
            or status_msg.startswith("Loading...")
            or status_msg.startswith("ERR:")
        )

    lcd.fill_rect(0, Y_POS - 1, lcd.width, BAR_HEIGHT, WHITE)
    lcd.text(status_msg, STATUS_X, Y_POS, BLACK)

    if show_id:
        device_id = "N/A"
        try:
            with open(DEVICE_ID_FILE, "r") as f:
                device_id = f.read().strip()
        except:
            pass

        id_text = "ID:{}".format(device_id)
        id_x = lcd.width - (len(id_text) * 8) - 5
        lcd.text(id_text, id_x, Y_POS, BLACK)

    _lcd_backlight_on()
    lcd.show()





def draw_boot_logo(lcd):
    if lcd is None:
        return

    expected = LOGO_W * LOGO_H * 2  # 40960 for 160x128 RGB565

    try:
        st = os.stat(LOGO_FILE)
        if st[6] != expected:
            print("Logo size mismatch. Expected", expected, "got", st[6])
            lcd.fill(BLACK)
        else:
            with open(LOGO_FILE, "rb") as f:
                f.readinto(lcd.buffer)
    except Exception as e:
        print("Logo error:", repr(e))
        lcd.fill(BLACK)

    # Do not call lcd.show() here.
    # draw_bottom_status() will draw the bar and call lcd.show() once.
    draw_bottom_status(lcd, "Connecting")

CURRENT_BRIGHTNESS = 70  # Set your desired level here (0-100)

def _lcd_backlight_on():
    """Manually controls the backlight pin since the driver lacks bl_ctrl."""
    try:
        # Import the pin definition directly from the driver file
        import Pico_LCD_1_8 as drv
        from machine import Pin, PWM
        
        # Initialize PWM on the Backlight Pin (drv.BL is usually Pin 13)
        bl_pin = Pin(drv.BL, Pin.OUT)
        pwm = PWM(bl_pin)
        pwm.freq(1000)
        
        # Convert 0-100% to 0-65535
        duty = int(CURRENT_BRIGHTNESS * 65535 / 100)
        pwm.duty_u16(duty)
        print("Backlight set to {}%".format(CURRENT_BRIGHTNESS))
    except Exception as e:
        print("Manual Backlight control failed:", e)

def init_lcd():
    if LCD_Driver is None:
        return None
    try:
        _lcd_hard_reset()
        
        # Create the driver object
        lcd = LCD_Driver()
        
        # Removed lcd.bl_ctrl(5) because it causes the AttributeError
        
        # Compatibility shim for .show() vs .show_up()
        if not hasattr(lcd, "show") and hasattr(lcd, "show_up"):
            def _show():
                lcd.show_up()
            lcd.show = _show

        # Use our manual PWM function instead of the driver's method
        _lcd_backlight_on()
        
        lcd.fill(BLACK)
        lcd.show()
        return lcd
    except Exception as e:
        print("LCD init failed:", repr(e))
        return None

def _lcd_hard_reset():
    # Hardware reset of LCD (prevents occasional hang after machine.reset())
    try:
        import Pico_LCD_1_8 as drv
        from machine import Pin

        # Backlight on before/after reset (helps visibility)
        try:
            Pin(drv.BL, Pin.OUT).value(1)
        except:
            pass

        rst = Pin(drv.RST, Pin.OUT)
        rst.value(0)
        time.sleep_ms(50)
        rst.value(1)
        time.sleep_ms(120)
    except:
        pass




def status_error(lcd, code):
    msg = "ERR:{:03d}".format(code)
    draw_bottom_status(lcd, msg)
    time.sleep(2)

## ---------- Wifi & Updates ----------
def load_config_wifi():
    try:
        import config
        ssid = getattr(config, "WIFI_SSID", None)
        pwd  = getattr(config, "WIFI_PASSWORD", None)

        # Normalize empty strings to None
        if ssid is not None:
            ssid = ssid.strip()
            if ssid == "":
                ssid = None
        if pwd is not None:
            pwd = str(pwd)

        return ssid, pwd
    except Exception:
        return None, None

def connect_wifi(lcd, ssid, pwd, timeout_sec=20, retries=2):
    if not ssid:
        return False

    draw_bottom_status(lcd, "Connecting")

    # Make sure AP mode is off
    try:
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
    except:
        pass

    sta = network.WLAN(network.STA_IF)

    for attempt in range(1, retries + 1):
        # Hard reset STA to avoid "first boot after update" flakiness
        try:
            sta.disconnect()
        except:
            pass
        try:
            sta.active(False)
        except:
            pass

        time.sleep_ms(400)

        sta.active(True)
        time.sleep_ms(400)

        try:
            sta.connect(ssid, pwd)
        except:
            pass

        t0 = time.ticks_ms()
        while True:
            if sta.isconnected():
                return True

            if time.ticks_diff(time.ticks_ms(), t0) > timeout_sec * 1000:
                break

            time.sleep_ms(150)

        # If attempt failed, pause briefly before retrying
        time.sleep_ms(800)

    # All attempts failed
    try:
        sta.active(False)
    except:
        pass
    return False




import socket



import uhashlib

def read_device_id():
    try:
        with open(DEVICE_ID_FILE, "r") as f:
            return f.read().strip()
    except:
        return ""

def _sha1_hex(s):
    try:
        h = uhashlib.sha1(s.encode("utf-8"))
        return ubinascii.hexlify(h.digest()).decode()
    except:
        return ""  # if hashing fails, replay protection is disabled


def fetch_control_json(lcd=None):
    url = gh_contents_url(CONTROL_PATH)
    print("BOOTLOADER: fetching (API)", CONTROL_PATH)

    r = None
    try:
        r = requests.get(url, headers=gh_api_headers_raw())
        print("BOOTLOADER: control.json HTTP:", r.status_code)
        if r.status_code != 200:
            return None
        return r.text
    except Exception as e:
        print("BOOTLOADER: fetch_control_json error:", repr(e))
        return None
    finally:
        try:
            if r:
                r.close()
        except:
            pass



def apply_control_if_needed(lcd=None):
    """
    Returns: force_update (bool)
    May reboot the device (and not return) if reboot is requested.
    Triggers only once per *changed* control.json (hash-based).
    """
    ctrl_text = fetch_control_json(lcd)
    if not ctrl_text:
        return False

    ctrl_hash = _sha1_hex(ctrl_text)

    last_hash = ""
    try:
        with open(CONTROL_HASH_FILE, "r") as f:
            last_hash = f.read().strip()
    except:
        pass

    # If unchanged since last time, do nothing (prevents reboot loops)
    if ctrl_hash and (last_hash == ctrl_hash):
        return False

    # Parse JSON
    try:
        ctrl = json.loads(ctrl_text)
    except Exception as e:
        print("BOOTLOADER: control.json parse error:", repr(e))
        return False

    dev_id = read_device_id()
    reboot_ids = ctrl.get("reboot_ids", []) or []
    force_ids  = ctrl.get("force_update_ids", []) or []

    want_reboot = (dev_id in reboot_ids)
    want_force  = (dev_id in force_ids)

    # Record hash BEFORE taking action so it only happens once per control.json content
    if ctrl_hash:
        try:
            with open(CONTROL_HASH_FILE, "w") as f:
                f.write(ctrl_hash)
            try:
                os.sync()
            except:
                pass
        except Exception as e:
            print("BOOTLOADER: failed to write control hash:", repr(e))

    if want_reboot:
        print("BOOTLOADER: CONTROL reboot requested for ID", dev_id)
        try:
            # Hide ID during reboot message (your draw_bottom_status supports show_id override)
            draw_bottom_status(lcd, "Rebooting", show_id=False)
        except:
            pass
        time.sleep(1)
        machine.reset()

    return True if want_force else False


def gh_contents_url(path):
    # API_BASE already points to .../contents/
    path = path.lstrip("/")
    return API_BASE + path + "?ref=" + GITHUB_BRANCH


import sys

def fetch_versions_json(lcd):
    url = gh_contents_url(VERSIONS_PATH)
    print("BOOTLOADER: fetching (API)", VERSIONS_PATH)
    r = None
    try:
        r = requests.get(url, headers=gh_api_headers_raw())
        print("BOOTLOADER: versions.json HTTP:", r.status_code)
        if r.status_code != 200:
            return None
        return json.loads(r.text)
    except Exception as e:
        print("BOOTLOADER: fetch_versions_json error:", repr(e))
        return None
    finally:
        try:
            if r:
                r.close()
        except:
            pass



def _ensure_dirs(filepath):
    # Create folders for targets like "fonts/small_font.py" if you ever use them
    if "/" not in filepath:
        return
    parts = filepath.split("/")[:-1]
    cur = ""
    for p in parts:
        cur = p if cur == "" else (cur + "/" + p)
        try:
            os.mkdir(cur)
        except:
            pass


def gh_download_to_file(path, out_path):
    url = gh_contents_url(path)
    r = None
    try:
        r = requests.get(url, headers=gh_api_headers_raw())
        print("GET", path, "HTTP:", r.status_code)
        if r.status_code != 200:
            return False

        _ensure_dirs(out_path)

        with open(out_path, "wb") as f:
            try:
                while True:
                    chunk = r.raw.read(1024)
                    if not chunk:
                        break
                    f.write(chunk)
            except:
                f.write(r.content)

        return True
    except Exception as e:
        print("Download error:", path, e)
        return False
    finally:
        try:
            if r:
                r.close()
        except:
            pass



def _safe_swap(target):
    tmp = target + ".new"
    bak = target + ".old"

    # remove old backup if present
    try:
        os.remove(bak)
    except:
        pass

    # backup current target if present
    try:
        os.rename(target, bak)
    except:
        pass

    # move tmp into place
    os.rename(tmp, target)

    # cleanup backup after successful swap
    try:
        os.remove(bak)
    except:
        pass
 
def perform_update(vers_data, lcd, force=False):
    SKIP = (
        "bootloader.py",
        "github_token.py",
        "config.py",
        "local_version.txt",
        "device_id.txt",
        "Pico_LCD_1_8.py",
    )

    # 0) Version compare
    local_v = "0.0.0"
    try:
        with open(LOCAL_VERSION_FILE, "r") as f:
            local_v = f.read().strip()
    except:
        pass

    remote_v = (vers_data.get("version") or "0.0.0").strip()
    print("BOOTLOADER: update needed", local_v, "->", remote_v, "(force={})".format(force))

    # Only skip when versions match AND we're not forcing
    if (not force) and (local_v == remote_v):
        print("BOOTLOADER: No update needed.")
        return True

    def _resolve(f_info):
        p = f_info.get("path")
        if not p:
            return None, None
        t = f_info.get("target") or p.split("/")[-1]
        return p, t

    def _updating(i, total):
        try:
            pct = int((i * 100) / total) if total else 100
            draw_bottom_status(lcd, "Updating {}%".format(pct), show_id=False)
        except:
            pass

    files = vers_data.get("files", [])
    if not files:
        print("BOOTLOADER: Manifest has no files.")
        return False

    # Build worklist (exclude skipped targets)
    work = []
    for f_info in files:
        path, target = _resolve(f_info)
        if not path or not target:
            continue
        if target in SKIP:
            continue
        work.append((path, target))

    total = len(work)
    if total == 0:
        print("BOOTLOADER: nothing to update (all skipped?)")
        return True

    # 1) Download all files to .new first (show progress here only)
    for idx, (path, target) in enumerate(work, start=1):
        _updating(idx, total)
        print("BOOTLOADER: downloading", idx, "/", total, target)

        tmp = target + ".new"
        ok = gh_download_to_file(path, tmp)
        if not ok:
            print("BOOTLOADER: Download failed:", path)
            return False
        gc.collect()

    # Lock status at 100% during swap (no second progress sweep)
    try:
        draw_bottom_status(lcd, "Updating 100%", show_id=False)
    except:
        pass

    # 2) Swap .new into place (no progress updates)
    for idx, (path, target) in enumerate(work, start=1):
        print("BOOTLOADER: swapping", idx, "/", total, target)
        try:
            _safe_swap(target)
        except Exception as e:
            print("BOOTLOADER: swap failed for", target, e)
            return False

    # 3) Update local version last (atomic + verified + flush)
    try:
        tmpv = LOCAL_VERSION_FILE + ".new"
        with open(tmpv, "w") as f:
            f.write(remote_v)

        try:
            os.remove(LOCAL_VERSION_FILE)
        except:
            pass
        os.rename(tmpv, LOCAL_VERSION_FILE)

        try:
            os.sync()
        except:
            pass

        time.sleep(0.5)

        with open(LOCAL_VERSION_FILE, "r") as f:
            print("BOOTLOADER: local_version now", f.read().strip())

    except Exception as e:
        print("BOOTLOADER: failed to write local_version:", repr(e))
        return False

    print("BOOTLOADER: Updated to", remote_v)

    #    # Show reboot message (no ID)
    try:
        draw_bottom_status(lcd, "Rebooting", show_id=False)
    except:
        pass

    # Cleanly shut down Wi-Fi before reset (reduces post-update weirdness)
    try:
        sta = network.WLAN(network.STA_IF)
        try:
            sta.disconnect()
        except:
            pass
        try:
            sta.active(False)
        except:
            pass
    except:
        pass

    time.sleep_ms(300)
    machine.reset()





def debug_list_root():
    url = API_BASE + "?ref=" + GITHUB_BRANCH
    h = gh_api_headers_raw()
    print("Auth header:", "Authorization" in h)
    print("URL:", url)
    r = requests.get(url, headers=h)
    print("HTTP:", r.status_code)
    print(r.text[:300])
    r.close()

def debug_versions():
    url = gh_contents_url(VERSIONS_PATH)
    h = gh_api_headers_raw()
    print("Auth header:", "Authorization" in h)
    print("URL:", url)
    r = requests.get(url, headers=h)
    print("HTTP:", r.status_code)
    print(r.text[:300])
    r.close()


# ---------- Runner ----------

import sys

def run_app_main(lcd=None):
    gc.collect()
    print("BOOTLOADER: handoff -> app_main")

    try:
        draw_bottom_status(lcd, "Loading", show_id=True)
    except:
        pass

    try:
        import app_main
        try:
            app_main.main(lcd)
        except TypeError:
            app_main.main()
    except Exception as e:
        print("APP CRASH:", repr(e))
        try:
            sys.print_exception(e)
        except:
            pass

        try:
            draw_bottom_status(lcd, "ERR:050", show_id=True)
        except:
            pass

        time.sleep(2)
        machine.reset()



def apply_staged_bootloader_if_present():
    try:
        if "bootloader.py.next" in os.listdir():
            print("BOOTLOADER: applying staged bootloader update")
            try:
                os.remove("bootloader.py.old")
            except:
                pass
            try:
                os.rename("bootloader.py", "bootloader.py.old")
            except:
                pass
            os.rename("bootloader.py.next", "bootloader.py")
            machine.reset()
    except Exception as e:
        print("BOOTLOADER: staged update apply failed:", repr(e))


def main():
    print("BOOTLOADER: main() start")
    apply_staged_bootloader_if_present()

    lcd = init_lcd()
    print("BOOTLOADER: lcd is", "OK" if lcd else "NONE")
    if lcd:
        draw_boot_logo(lcd)

    ssid, pwd = load_config_wifi()
    if not ssid:
        status_error(lcd, 20)
        run_app_main(lcd)
        return

    # Small settle delay right after boot (helps after machine.reset())
    time.sleep_ms(800)

    if not connect_wifi(lcd, ssid, pwd, timeout_sec=20, retries=2):
        status_error(lcd, 0)  # ERR:000 = wifi connect failed
        run_app_main(lcd)
        return


    # NEW: control.json actions
    force_update = apply_control_if_needed(lcd)

    vers_data = fetch_versions_json(lcd)
    if vers_data:
        perform_update(vers_data, lcd, force=force_update)

    run_app_main(lcd)



if __name__ == "__main__":
    main()


