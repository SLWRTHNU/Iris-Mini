import utime as time
import network
import urequests as requests
import json
import os
import gc
import ubinascii
import machine
import sys
import utime

# 0. SPEED BOOST: Overclock to 240MHz 
machine.freq(240000000)

def log(msg):
    timestamp = time.ticks_ms()
    print("[{:>8}ms] {}".format(timestamp, msg))

# ---------- GitHub & Paths ----------
GITHUB_USER   = "SLWRTHNU"
GITHUB_REPO   = "Iris-Mini"
GITHUB_BRANCH = "main"
RAW_BASE_URL  = "https://raw.githubusercontent.com/{}/{}/{}/".format(GITHUB_USER, GITHUB_REPO, GITHUB_BRANCH)
API_BASE      = "https://api.github.com/repos/{}/{}/contents/".format(GITHUB_USER, GITHUB_REPO)

VERSIONS_PATH = "versions.json"
LOCAL_VERSION_FILE = "local_version.txt"
DEVICE_ID_FILE     = "device_id.txt"

CURRENT_BRIGHTNESS = 100

def _get_token():
    try:
        import github_token
        return getattr(github_token, "GITHUB_TOKEN", "")
    except:
        return ""

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
        LCD_Driver = None

BLACK = 0x0000
WHITE = 0xFFFF
LOGO_FILE   = "logo.bin"
LOGO_W      = 160
LOGO_H      = 128
TEXT_HEIGHT = 10
BAR_HEIGHT  = TEXT_HEIGHT + 1
Y_POS       = 128 - BAR_HEIGHT + 1
STATUS_X    = 3

# ---------- LCD Logic (CLEANED) ----------

def _lcd_backlight_on():
    try:
        import Pico_LCD_1_8 as drv
        from machine import Pin, PWM
        # Using PWM ensures the screen actually gets enough power to light up
        bl_pin = Pin(drv.BL, Pin.OUT)
        pwm = PWM(bl_pin)
        pwm.freq(1000)
        duty = int(100 * 65535 / 100) # Force 100%
        pwm.duty_u16(duty)
    except:
        # Fallback if PWM fails
        try:
            from machine import Pin
            import Pico_LCD_1_8 as drv
            Pin(drv.BL, Pin.OUT).value(1)
        except: pass

def _lcd_hard_reset():
    try:
        import Pico_LCD_1_8 as drv
        from machine import Pin
        rst = Pin(drv.RST, Pin.OUT)
        # Sequence: High -> Low (Reset) -> High (Run)
        rst.value(1)
        time.sleep_ms(10)
        rst.value(0)
        time.sleep_ms(100) # Standard ST7735 reset pulse
        rst.value(1)
        time.sleep_ms(150) # Critical: Wait for controller to wake up
    except: pass

def init_lcd():
    if LCD_Driver is None: 
        log("Driver not found")
        return None
    try:
        _lcd_hard_reset()
        lcd = LCD_Driver()
        
        # We now know for sure the method is .show()
        # We'll create a standard alias to stay safe
        lcd.display_update = lcd.show
            
        lcd.fill(BLACK) # Kill the colorful noise
        lcd.display_update()
        _lcd_backlight_on()
        return lcd
    except Exception as e:
        log("LCD Init Error: {}".format(e))
        return None

def draw_bottom_status(lcd, status_msg, show_id=None):
    if lcd is None: 
        return

    # 1. Determine if we should show the Device ID
    # Usually we show it during connection or if an error occurs
    if show_id is None:
        show_id = any(status_msg.startswith(x) for x in ["Connecting", "Connected", "ERR:"])

    # 2. Draw the Status Bar Background (White bar at the bottom)
    # Y_POS is calculated as: 128 (height) - 11 (bar height) + 1 = 118
    lcd.fill_rect(0, Y_POS - 1, lcd.width, BAR_HEIGHT, WHITE)
    
    # 3. Draw the Status Text (Black text on white bar)
    lcd.text(status_msg, STATUS_X, Y_POS, BLACK)

    # 4. Draw Device ID if required
    if show_id:
        device_id = "N/A"
        try:
            if DEVICE_ID_FILE in os.listdir():
                with open(DEVICE_ID_FILE, "r") as f:
                    device_id = f.read().strip()
        except: 
            pass
            
        id_text = "ID:{}".format(device_id)
        # Calculate X position to right-align the ID text
        # (8 pixels per character)
        id_x = lcd.width - (len(id_text) * 8) - 3
        lcd.text(id_text, id_x, Y_POS, BLACK)

    # 5. Push the update to the physical screen
    lcd.show()
    
def draw_boot_logo(lcd):
    if lcd is None: return
    # 160x128 * 2 bytes (RGB565) = 40,960 bytes
    expected = 40960 
    
    try:
        # Check file before trying to read it
        st = os.stat(LOGO_FILE)
        if st[6] == expected:
            with open(LOGO_FILE, "rb") as f:
                # Directly fill the driver's internal buffer
                f.readinto(lcd.buffer)
            log("Logo binary applied to buffer")
        else:
            log("Logo size wrong: {} bytes".format(st[6]))
            lcd.fill(BLACK)
    except Exception as e:
        log("Logo File Error: {}".format(e))
        lcd.fill(BLACK)
        
    # Send the buffer (either logo or black) to the hardware
    lcd.show() 
    draw_bottom_status(lcd, "Connecting")

# ---------- WiFi & Updates ----------

def load_config_wifi():
    try:
        import config
        ssid = getattr(config, "WIFI_SSID", None)
        pwd  = getattr(config, "WIFI_PASSWORD", None)
        if ssid: ssid = ssid.strip()
        if pwd: pwd = str(pwd)
        return ssid, pwd
    except ImportError:
        # This specifically catches when config.py is missing
        return None, None
    except Exception:
        return None, None

def connect_wifi(lcd, ssid, pwd, timeout_sec=15, retries=2):
    if not ssid:
        log("WiFi Error: No SSID")
        return False

    draw_bottom_status(lcd, "Connecting")
    
    # 1. Ensure Access Point is fully OFF
    ap = network.WLAN(network.AP_IF)
    if ap.active():
        ap.active(False)
        time.sleep_ms(500)

    sta = network.WLAN(network.STA_IF)
    
    try:
        network.hostname("Iris-Mini")
    except: pass

    for attempt in range(1, retries + 1):
        log("WiFi Attempt {}/{}".format(attempt, retries))
        
        # 2. Hard reset the STA interface for a clean slate
        sta.active(False)
        time.sleep_ms(500)
        sta.active(True)
        
        # 3. Explicitly disconnect before starting a new handshake
        sta.disconnect()
        time.sleep_ms(200)
        
        sta.connect(ssid, pwd)

        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < timeout_sec * 1000:
            status = sta.status()
            
            # 0=Idle, 1=Connecting, 2=No Route, 3=Connected, -1=Fail, -2=Bad Password
            if utime.ticks_ms() % 1000 < 100: # Log once per second
                log("WiFi Status: {}".format(status))
            
            # 4. Check for success
            if sta.isconnected():
                log("WiFi Connected! IP: " + sta.ifconfig()[0])
                return True
            
            if status < 0 or status == 201: 
                log("WiFi Error: Bad Auth/Failure ({})".format(status))
                break
                
            utime.sleep_ms(250)
        
        log("Attempt {} timed out.".format(attempt))
        time.sleep_ms(1000)

    return False

def gh_contents_url(path):
    return API_BASE + path.lstrip("/") + "?ref=" + GITHUB_BRANCH

def fetch_versions_json(lcd):
    url = gh_contents_url(VERSIONS_PATH)
    r = None
    try:
        r = requests.get(url, headers=gh_api_headers_raw())
        if r.status_code != 200: return None
        return json.loads(r.text)
    except: return None
    finally:
        if r: r.close()

def gh_download_to_file(path, out_path):
    url = gh_contents_url(path)
    r = None
    try:
        r = requests.get(url, headers=gh_api_headers_raw())
        if r.status_code != 200: return False
        
        if "/" in out_path:
            parts = out_path.split("/")[:-1]
            cur = ""
            for p in parts:
                cur = p if cur == "" else (cur + "/" + p)
                try: os.mkdir(cur)
                except: pass

        with open(out_path, "wb") as f:
            try:
                while True:
                    chunk = r.raw.read(1024)
                    if not chunk: break
                    f.write(chunk)
            except: f.write(r.content)
        return True
    except: return False
    finally:
        if r: r.close()

def _safe_swap(target):
    tmp, bak = target + ".new", target + ".old"
    try: os.remove(bak)
    except: pass
    try: os.rename(target, bak)
    except: pass
    os.rename(tmp, target)
    try: os.remove(bak)
    except: pass
 

def perform_update(vers_data, lcd, force=False):
    SKIP = ("bootloader.py", "github_token.py", "config.py", "local_version.txt", "Pico_LCD_1_8.py")
    remote_v = (vers_data.get("version") or "0.0.0").strip()
    
    files = vers_data.get("files", [])
    work = []
    for f in files:
        p = f.get("path")
        t = f.get("target") or p.split("/")[-1]
        if t not in SKIP: 
            work.append((p, t))

    if not work: return True

    # 1. DOWNLOAD
    for idx, (p, t) in enumerate(work, start=1):
        pct = int((idx * 100) / len(work))
        log("Downloading: {} ({}%)".format(t, pct))
        if lcd: draw_bottom_status(lcd, "Updating {}%".format(pct), show_id=False)
        if not gh_download_to_file(p, t + ".new"): return False
        gc.collect()

    # 2. COMMIT
    log("Swapping files...")
    if lcd: draw_bottom_status(lcd, "Saving...", show_id=False)
    for p, t in work: 
        _safe_swap(t)

    with open(LOCAL_VERSION_FILE, "w") as f: f.write(remote_v)
    
    try:
        import os
        os.sync() 
    except: pass
    
    # 3. THE HARD RESET (The most important part)
    log("REBOOTING NOW")
    if lcd:
        draw_bottom_status(lcd, "Rebooting...", show_id=False)
    
    time.sleep_ms(1200) # Give the screen time to show the text
    
    # Force the hardware to cycle power
    machine.WDT(timeout=10) 
    while True: pass
    

def run_app_main(lcd=None):
    gc.collect()
    log("Handoff -> app_main")
    try: draw_bottom_status(lcd, "Connected ", show_id=True)
    except: pass
    try:
        import app_main
        app_main.main(lcd)
    except Exception as e:
        print("APP CRASH:", e)
        time.sleep(2)
        machine.reset()

def apply_staged_bootloader_if_present():
    if "bootloader.py.next" in os.listdir():
        try:
            os.rename("bootloader.py.next", "bootloader.py")
            machine.reset()
        except: pass

# ---------- Runner ----------

def main():
    log("BOOTLOADER: Starting...")
    apply_staged_bootloader_if_present()
    
    # 1. Check for config.py before doing anything else
    config_exists = False
    try:
        os.stat("config.py")
        config_exists = True
    except OSError:
        config_exists = False

    # 2. If no config, enter Setup Mode
    if not config_exists:
        lcd = init_lcd()
        # Start Access Point
        ap = network.WLAN(network.AP_IF)
        ap.active(True)
        
        # This is the safest way to set an Open network across different firmware versions
        ap.config(essid="Iris Mini", security=0)
        
        ip = "192.168.4.1" # Standard MicroPython AP IP
        
        if lcd:
            # Assumes: BLACK = 0x0000, WHITE = 0xFFFF
            # Note: 0xFFE0 is yellow in RGB565
            YELLOW = 0xF81F

            def _text_w_px(s):  # built-in 8px font in most Pico LCD drivers
                return len(s) * 8

            def text_center(s, y, color, w=160):
                x = max(0, (w - _text_w_px(s)) // 2)
                lcd.text(s, x, y, color)

            # If your display is 128x160 (portrait), change w/h accordingly.
            w = 160
            h = 128

            M   = 8    # outer margin
            LH  = 14   # line height
            IND = 12   # indent for sub-lines

            lcd.fill(BLACK)

            # Title block
            text_center("IRIS SETUP", M, YELLOW, w=w)
            text_center("Follow these steps:", M + LH, WHITE, w=w)

            y = M + (LH * 3)

            # Step 1
            lcd.text("1) Connect to WiFi:", M, y, WHITE); y += LH
            lcd.text("   Iris Mini", M + IND, y, YELLOW); y += (LH + 15)

            # Step 2
            lcd.text("2) Open this URL:", M, y, WHITE); y += LH
            url = "{}".format(ip) if ip else "http://(starting...)"

            # Split if needed so it never hugs the right edge
            if _text_w_px(url) > (w - (M + IND)):
                lcd.text("   http://", M + IND, y, YELLOW); y += LH
                lcd.text("   {}".format(ip if ip else "(starting...)"), M + IND, y, YELLOW)
            else:
                lcd.text("   {}".format(url), M + IND, y, YELLOW)

            lcd.show()
        # --- end setup screen ---

        log("Config missing. Setup Mode active at http://" + ip)

        # We will build this file next
        import setup_server
        setup_server.run() 
        
        # Add these lines right after the server finishes/machine resets
        ap = network.WLAN(network.AP_IF)
        ap.active(False) 
        log("Setup complete. AP disabled.")
        return # Stop here so it doesn't try to run the app

    # 3. NORMAL BOOT (If config exists)
    lcd = init_lcd()
    if lcd: draw_boot_logo(lcd)

    ssid, pwd = load_config_wifi()
    
    # Attempt connection
    if not ssid or not connect_wifi(lcd, ssid, pwd):
        log("WiFi Failed. Halting.")
        if lcd:
            lcd.fill(0x0000) # BLACK
            
            # Title
            lcd.text("WIFI FAILED", 40, 15, 0xFC00) # RED
            
            # Instructions
            lcd.text("1. Power cycle", 10, 40, 0xFFFF) # WHITE
            lcd.text("   your Iris", 10, 50, 0xFFFF)
            
            lcd.text("2. Power cycle", 10, 70, 0xFFFF)
            lcd.text("   your router", 10, 80, 0xFFFF)
            
            lcd.text("3. Factory Reset", 10, 100, 0xFFFF)
            lcd.text("   to reconfigure", 10, 110, 0xFFFF)
            
            lcd.show()
        
        # Stop execution so they can read the screen
        return

    # If we get here, WiFi is successful
    log("Checking GitHub...")
    if lcd:
        draw_bottom_status(lcd, "Connecting", show_id=True)
    
    vers_data = fetch_versions_json(lcd)
    
    if vers_data:
        # 1. Handle remote reboot command
        if vers_data.get("remote_command") == "reboot":
            log("Remote reboot command received.")
            machine.reset()
            
        # 2. Compare versions
        remote_v = (vers_data.get("version") or "0.0.0").strip()
        local_v = "0.0.0"
        try:
            with open(LOCAL_VERSION_FILE, "r") as f: local_v = f.read().strip()
        except: pass

        force_update = vers_data.get("force_update", False)

        # Update if force is True OR if versions don't match
        if (local_v != remote_v) or force_update:
            log("Update required ({} -> {}). Starting...".format(local_v, remote_v))
            # perform_update contains the hardware reset
            perform_update(vers_data, lcd, force=True) 
            return # IMPORTANT: Stop main() execution here!
            
        log("No update needed (Version {}).".format(local_v))
    
    # 3. Only run the app if we didn't start an update
    run_app_main(lcd)

if __name__ == "__main__":
    main()


