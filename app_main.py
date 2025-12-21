# app_main.py (Iris Classic 1.8")
import utime
import network
import ntptime
import urequests as requests
import gc
from machine import Timer
import control_poll

# Optional: not all MicroPython builds have setdefaulttimeout()
try:
    import socket
    if hasattr(socket, "setdefaulttimeout"):
        socket.setdefaulttimeout(2)
except Exception:
    pass

# ---------- Config ----------
from config import * # noqa

# ---------- Display driver ----------
# Adjusted for 1.8" Display
try:
    from Pico_LCD_1_8 import LCD_1inch8 as LCD_Driver
except ImportError:
    try:
        from ST7735 import ST7735 as LCD_Driver
    except ImportError:
        raise RuntimeError("Missing 1.8 inch LCD driver (Pico_LCD_1_8.py or ST7735.py)")

# ---------- Fonts / Writer ----------
from writer import CWriter
import small_font as font_small
import large_font as font_big
import arrows_font as font_arrows
import heart as font_heart

# ---------- Colors ----------
BLACK  = 0x0000
WHITE  = 0xFFFF
RED    = 0xF800
YELLOW = 0xF81F

# --- Global Heart State ---
hb_state = True

# ---------- Helpers ----------
def get_device_id():
    """Reads the persistent ID created by the bootloader."""
    try:
        with open("device_id.txt", "r") as f:
            return f.read().strip()
    except Exception:
        return "N/A"

def connect_wifi(ssid, pwd, timeout_sec=12):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        return True
    sta.connect(ssid, pwd)
    t0 = utime.ticks_ms()
    while not sta.isconnected():
        if utime.ticks_diff(utime.ticks_ms(), t0) > timeout_sec * 1000:
            return False
        utime.sleep(0.25)
    return True

def ntp_sync(retries=3, delay_s=1):
    for _ in range(retries):
        try:
            ntptime.settime()
            return True
        except Exception:
            utime.sleep(delay_s)
    return False

def ensure_count2(endpoint: str) -> str:
    if "count=" in endpoint:
        return endpoint.replace("count=1", "count=2")
    joiner = "&" if "?" in endpoint else "?"
    return endpoint + joiner + "count=2"

def fetch_ns_entries():
    headers = {}
    if NS_TOKEN:
        headers["api-secret"] = NS_TOKEN
    ep = ensure_count2(API_ENDPOINT)
    url = NS_URL + ep
    resp = None
    try:
        resp = requests.get(url, headers=headers)
        data = resp.json()
        resp.close()
        return data
    except Exception:
        try:
            if resp: resp.close()
        except Exception: pass
        return None

def mgdl_to_units(val_mgdl: float) -> float:
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return float(val_mgdl)
    return round(float(val_mgdl) / 18.0, 1)

def direction_to_arrow(direction: str) -> str:
    return {
        "Flat": "A",
        "SingleUp": "C",
        "DoubleUp": "CC",
        "TripleUp": "CCC",
        "SingleDown": "D",
        "DoubleDown": "DD",
        "TripleDown": "DDD",
        "FortyFiveUp": "G",
        "FortyFiveDown": "H",
        "NOT COMPUTABLE": "--",
        "NONE": "--",
    }.get(direction or "NONE", "")

def parse_entries(data):
    if not data or not isinstance(data, list) or len(data) < 1:
        return None
    cur = data[0]
    if "sgv" not in cur or "date" not in cur:
        return None
    cur_mgdl = cur["sgv"]
    cur_time_ms = cur["date"]
    direction = cur.get("direction", "NONE")
    delta_units = None
    if len(data) > 1 and isinstance(data[1], dict) and "sgv" in data[1]:
        prev_mgdl = data[1]["sgv"]
        delta_mgdl = float(cur_mgdl) - float(prev_mgdl)
        if str(DISPLAY_UNITS).lower() == "mgdl":
            delta_units = float(delta_mgdl)
        else:
            delta_units = round(delta_mgdl / 18.0, 1)
    return {
        "bg": mgdl_to_units(cur_mgdl),
        "time_ms": int(cur_time_ms),
        "direction": direction,
        "arrow": direction_to_arrow(direction),
        "delta": delta_units,
    }

def fmt_bg(bg_val: float) -> str:
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return str(int(round(bg_val)))
    return "{:.1f}".format(bg_val)

def fmt_delta(delta_val) -> str:
    if delta_val is None: return ""
    if str(DISPLAY_UNITS).lower() == "mgdl":
        return "{:+.0f}".format(delta_val)
    return "{:+.1f}".format(delta_val)

UNIX_2000_OFFSET = 946684800  # seconds between 1970-01-01 and 2000-01-01

def now_unix_s():
    t = utime.time()
    # Many MicroPython ports (including RP2) use a 2000-based epoch.
    # Nightscout's `date` is Unix epoch (1970) in ms.
    if t < 1200000000:  # heuristic: "too small" to be modern Unix time
        return t + UNIX_2000_OFFSET
    return t


def draw_screen(lcd, w_small, w_big, w_arrow, w_heart, last, hb_state): 
    # --- LOADING STATE (Status bar over logo) ---
    if not last:
        # 1.8" screen is 128px high. 
        TEXT_HEIGHT = 7
        BAR_HEIGHT  = TEXT_HEIGHT + 4
        # Positioning the white status bar at the bottom of 128px height
        Y_POS       = 128 - BAR_HEIGHT + 1
        STATUS_X    = 5
        
        device_id = get_device_id()
        id_text = "ID:{}".format(device_id)

        # Draw a WHITE bar so BLACK text is visible
        lcd.fill_rect(0, Y_POS, lcd.width, BAR_HEIGHT, WHITE)
        
        # Use WHITE for text if the background is BLACK, 
        # or BLACK text if the bar is WHITE. Let's do Black text on White bar:
        lcd.text("Loading...", STATUS_X, Y_POS + 1, BLACK)
        
        id_x = lcd.width - (len(id_text) * 8) - 5
        lcd.text(id_text, id_x, Y_POS + 2, BLACK)
        
        lcd.show()
        return         

    # --- DATA STATE (Full UI) ---
    lcd.fill(BLACK) # This clears the logo/status bar once data arrives
    
    # ... (Rest of your data drawing code stays the same)

    W, H = lcd.width, lcd.height # 160, 128
    M = 4 # Reduced margin for smaller screen
    
    # 1. Extract and Process Data
    raw_s = last["time_ms"] // 1000
    
    age_s = now_unix_s() - raw_s
    if age_s < 0:
        age_s = 0
        
    # Rounding helps match what you see in Nightscout’s UI more closely
    mins = int((age_s + 30) // 60)  # +30s = round-to-nearest-minute

    bg_val = last["bg"]
    direction = last["direction"]
    
    bg_text = fmt_bg(bg_val)
    arrow_text = last["arrow"]
    delta_text = fmt_delta(last["delta"])
    age_text = "{} {} ago".format(mins, "min" if mins == 1 else "mins")
    
    # 2. Threshold Logic
    age_color = RED if mins >= STALE_MIN else WHITE
    bg_color = WHITE
    if bg_val <= LOW_THRESHOLD:
        bg_color = RED
    elif bg_val >= HIGH_THRESHOLD:
        bg_color = YELLOW
        
    arrow_color = WHITE
    if ALERT_DOUBLE_UP and direction == "DoubleUp":
        arrow_color = YELLOW
    elif ALERT_DOUBLE_DOWN and direction == "DoubleDown":
        arrow_color = RED

    # 3. Layout Calculations
    small_h, big_h, arrow_h, heart_h = font_small.height(), font_big.height(), font_arrows.height(), font_heart.height()
    bottom_h = max(small_h, arrow_h)

    y_age = M
    y_bg = (H - big_h) // 2
    y_bottom_base = H - bottom_h - M
    y_arrow = y_bottom_base + (bottom_h - arrow_h) // 2
    y_delta = y_bottom_base + (bottom_h - small_h) // 2

    # Draw Age
    w_small.setcolor(BLACK,age_color)
    age_w = w_small.stringlen(age_text)
    x_age = (W - age_w) // 2
    w_small.set_textpos(lcd, y_age, x_age)
    w_small.printstring(age_text)

    # Draw Heart
    if hb_state:
        w_heart.setcolor(BLACK, RED)
        # Reduced heart gap to 5 to save space
        w_heart.set_textpos(lcd, y_age + (small_h - heart_h) // 2, x_age + age_w + 5)
        w_heart.printstring("T")

    # Draw BG
    w_big.setcolor(BLACK, bg_color)
    x_bg = (W - w_big.stringlen(bg_text)) // 2
    w_big.set_textpos(lcd, y_bg, x_bg)
    w_big.printstring(bg_text)

    # Draw Trend Arrow
    w_arrow.setcolor(BLACK, arrow_color)
    # Use y_arrow (calculated) and M (margin)
    w_arrow.set_textpos(lcd, y_arrow, M) 
    w_arrow.printstring(arrow_text)

    # Draw Delta
    if delta_text:
        w_small.setcolor(BLACK, WHITE)
        x_delta = W - M - w_small.stringlen(delta_text)
        w_small.set_textpos(lcd, y_delta, x_delta)
        w_small.printstring(delta_text)

    lcd.show()

def main(lcd=None):
    global hb_state
    gc.collect()

    if lcd is None:
        # Initializing the 1.8" Driver
        lcd = LCD_Driver()

    # Apply .show() shim if driver uses show_up()
    if not hasattr(lcd, "show") and hasattr(lcd, "show_up"):
        def _show():
            lcd.show_up()
        lcd.show = _show

    w_small = CWriter(lcd, font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_small.set_spacing(3) # Tighter spacing for 1.8"
    w_big = CWriter(lcd, font_big, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow = CWriter(lcd, font_arrows, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_heart = CWriter(lcd, font_heart, fgcolor=RED, bgcolor=BLACK, verbose=False)
    w_arrow.set_spacing(8) # Tighter spacing for 1.8"

    # Initial Loading Call
    draw_screen(lcd, w_small, w_big, w_arrow, w_heart, None, hb_state)

    connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    ntp_sync()
    ok = ntp_sync()
    print("NTP:", "OK" if ok else "FAILED", "now_unix_s:", now_unix_s())

    FETCH_MS = 15000
    last = None
    fetch_next = utime.ticks_ms()
    last_hb_state = not hb_state

    def tick(t):
        global hb_state
        hb_state = not hb_state

    blink_timer = Timer()
    blink_timer.init(period=1500, mode=Timer.PERIODIC, callback=tick)

    while True:
        now = utime.ticks_ms()

        if hb_state != last_hb_state:
            last_hb_state = hb_state
            draw_screen(lcd, w_small, w_big, w_arrow, w_heart, last, hb_state)

        if utime.ticks_diff(now, fetch_next) >= 0:
            data = fetch_ns_entries()
            parsed = parse_entries(data)
            if parsed:
                last = parsed
            fetch_next = utime.ticks_add(now, FETCH_MS)

        # Remote control poll (reboot / force update). Internally rate-limited.
        control_poll.tick(lcd)

        utime.sleep_ms(10)


if __name__ == "__main__":
    main()

