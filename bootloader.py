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

def updating_progress(lcd, i, total, phase="Updating"):
    msg = "{} {}/{}".format(phase, i, total)
    draw_bottom_status(lcd, msg)

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

def draw_bottom_status(lcd, status_msg):
    if lcd is None:
        return

    # Right-side ID
    device_id = "N/A"
    try:
        with open(DEVICE_ID_FILE, "r") as f:
            device_id = f.read().strip()
    except:
        pass
    id_text = "ID:{}".format(device_id)

    # Bottom bar
    lcd.fill_rect(0, Y_POS - 1, lcd.width, BAR_HEIGHT, WHITE)

    # Left status text
    lcd.text(status_msg, STATUS_X, Y_POS, BLACK)

    # Right ID text
    id_x = lcd.width - (len(id_text) * 8) - 5
    lcd.text(id_text, id_x, Y_POS, BLACK)

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

    # Only one screen refresh: the bottom status draws + calls lcd.show()
    draw_bottom_status(lcd, "Connecting")

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

def draw_boot_logo(lcd):
    if lcd is None:
        return

    expected = LOGO_W * LOGO_H * 2  # 160*128*2 = 40960

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

    # IMPORTANT: do not call lcd.show() here
    # draw_bottom_status() will call lcd.show() once after it draws the bar
    draw_bottom_status(lcd, "Connecting")


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

import socket


def wait_for_internet_ready(max_s=5):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < max_s * 1000:
        try:
            socket.getaddrinfo("api.github.com", 443)
            return True
        except Exception as e:
            time.sleep_ms(250)
    return False


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
 

def perform_update(vers_data, lcd):
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
    print("BOOTLOADER: update needed", local_v, "->", remote_v)

    if local_v == remote_v:
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
            draw_bottom_status(lcd, "Updating {}%".format(pct))
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
        draw_bottom_status(lcd, "Updating 100%")
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

    try:
        draw_bottom_status(lcd, "Rebooting")
    except:
        pass

    gc.collect()
    time.sleep(2)
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
        draw_bottom_status(lcd, "Loading")
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
            draw_bottom_status(lcd, "ERR:050")
        except:
            pass
        time.sleep(2)




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

    # 0) Apply staged bootloader update once
    try:
        apply_staged_bootloader_if_present()
    except Exception as e:
        print("BOOTLOADER: staged update apply failed:", repr(e))

    # 1) LCD init
    lcd = None
    try:
        lcd = init_lcd()
    except Exception as e:
        print("BOOTLOADER: init_lcd failed:", repr(e))

    print("BOOTLOADER: lcd is", "OK" if lcd else "NONE")

    # 2) Draw logo + show Connecting
    try:
        if lcd:
            draw_boot_logo(lcd)  # should end by calling draw_bottom_status(...,"Connecting")
    except Exception as e:
        print("BOOTLOADER: draw_boot_logo failed:", repr(e))
        try:
            sys.print_exception(e)
        except:
            pass
        try:
            draw_bottom_status(lcd, "ERR:010")
        except:
            pass

    # 3) Wi-Fi credentials
    try:
        ssid, pwd = load_config_wifi()
    except Exception as e:
        print("BOOTLOADER: load_config_wifi failed:", repr(e))
        try:
            sys.print_exception(e)
        except:
            pass
        try:
            draw_bottom_status(lcd, "ERR:020")
        except:
            pass
        run_app_main(lcd)
        return

    if not ssid:
        print("BOOTLOADER: no ssid")
        status_error(lcd, 20)
        run_app_main(lcd)
        return

    # 4) Connect Wi-Fi
    if not connect_wifi(lcd, ssid, pwd):
        print("BOOTLOADER: wifi connect failed")
        status_error(lcd, 0)
        run_app_main(lcd)
        return

    # 5) Internet ready
    if not wait_for_internet_ready(5):
        print("BOOTLOADER: internet not ready")
        status_error(lcd, 1)
        run_app_main(lcd)
        return

    # 6) Update check
    vers_data = None
    try:
        vers_data = fetch_versions_json(lcd)
    except Exception as e:
        print("BOOTLOADER: fetch_versions_json failed:", repr(e))
        try:
            sys.print_exception(e)
        except:
            pass

    print("BOOTLOADER: vers_data is", "present" if vers_data else "NONE")

    if vers_data:
        try:
            perform_update(vers_data, lcd)
        except Exception as e:
            print("BOOTLOADER: perform_update failed:", repr(e))
            try:
                sys.print_exception(e)
            except:
                pass
            try:
                draw_bottom_status(lcd, "ERR:030")
            except:
                pass

    # 7) Hand off
    print("BOOTLOADER: about to handoff")
    try:
        draw_bottom_status(lcd, "Loading...")
    except:
        pass

    run_app_main(lcd)




if __name__ == "__main__":
    main()

