import utime
import network
import urequests as requests
import ntptime
import machine
import socket
import uos as os  # filesystem
import gc
import utime
import json

def net_diag():
    try:
        import network, socket
    except ImportError:
        print("net_diag: missing network/socket modules")
        return

    wlan = network.WLAN(network.STA_IF)
    print("=== NET DIAG ===")
    print("status:", wlan.status(), "isconnected:", wlan.isconnected())
    try:
        print("ifconfig:", wlan.ifconfig())
    except Exception as e:
        print("ifconfig error:", e)

    # DNS test to your Nightscout host
    host = "sennaloop-673ad2782247.herokuapp.com"
    try:
        info = socket.getaddrinfo(host, 443)
        print("DNS OK:", info)
    except OSError as e:
        print("DNS error:", e)

    # Raw IP route test
    try:
        addr = socket.getaddrinfo("1.1.1.1", 80)[0][-1]
        s = socket.socket()
        s.settimeout(5)
        s.connect(addr)
        print("RAW IP OK (1.1.1.1:80)")
        s.close()
    except OSError as e:
        print("RAW IP error:", e)

    print("=== END NET DIAG ===")


# ------------------------
# Heartbeat / memory monitor
# ------------------------
last_heartbeat_ms = None

def heartbeat(tag="OK"):
    """
    Print a status line with RAM usage about every 10 seconds.
    Uses utime.ticks_ms() so it works reliably in MicroPython.
    """
    global last_heartbeat_ms
    now = utime.ticks_ms()
    if last_heartbeat_ms is None or utime.ticks_diff(now, last_heartbeat_ms) >= 10000:
        gc.collect()
        print(
            "HB", tag,
            "| free RAM:", gc.mem_free(),
            "| alloc RAM:", gc.mem_alloc(),
        )
        last_heartbeat_ms = now


# ---------- Display driver ----------
try:
    from Pico_LCD_1_8 import LCD_1inch8 as ST7735
except ImportError:
    print("FATAL: Display not found. Rebooting in 5 seconds...")
    utime.sleep(5)
    machine.reset()

from writer import CWriter
import small_20 as font_main
import large_65 as font_bg
import arrows_30 as font_arrows

# ---------- Colours ----------
BLACK  = 0x0000
WHITE  = 0xffff
RED    = 0x07E0
GREEN  = 0x07E0
YELLOW = 0xc5df
BLUE   = 0x1200

# ---------- Display / font globals ----------
DISPLAY_WIDTH  = 128
DISPLAY_HEIGHT = 160

SMALL_H = font_main.height()
BG_H    = font_bg.height()
ARROWS  = font_arrows.height()
LINE_GAP = 8

wri_small  = None
wri_bg     = None
wri_arrows = None

SYNC_INTERVAL = 60  # NTP re-sync cycles

# ---------- Config globals ----------
WIFI_SSID     = ""
WIFI_PASSWORD = ""
NS_URL        = ""
NS_TOKEN      = ""
API_ENDPOINT  = ""

# Alert/display config (with defaults)
DISPLAY_UNITS      = "mmol"  # "mmol" or "mgdl"
STALE_MIN          = 7.0
LOW_THRESHOLD      = 4.0
HIGH_THRESHOLD     = 11.0
ALERT_DOUBLE_UP    = True
ALERT_DOUBLE_DOWN  = True

# ---------- Detect existing config ----------
try:
    import config as user_config
    if hasattr(user_config, "WIFI_SSID") and hasattr(user_config, "NS_URL"):
        HAS_CONFIG = True
    else:
        user_config = None
        HAS_CONFIG = False
except ImportError:
    user_config = None
    HAS_CONFIG = False


def center_x(writer, text: str) -> int:
    width = writer.stringlen(text)
    return (DISPLAY_WIDTH - width) // 2


def check_factory_reset(lcd):
    """
    Hold GP15 LOW at boot to perform a factory reset:
    - Deletes config.py (if present)
    - Reboots, which will fall back to AP config portal
    Wiring: GP15 -> momentary button -> GND (active-low).
    If no button is wired, pull-up keeps it inactive.
    """
    RESET_PIN = 15
    try:
        pin = machine.Pin(RESET_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    except Exception as e:
        print("Factory reset failed:", e)
        return

    if pin.value() == 0:
        lcd.fill(BLACK)
        lcd.text("Factory reset", 5, 40, WHITE)
        lcd.text("Erasing config", 5, 60, WHITE)
        lcd.show()
        try:
            os.remove("config.py")
            print("Factory reset complete")
            lcd.text("Factory reset OK", 5, 80, WHITE)
        except OSError as e:
            print("Factory reset: no config.py or error:", e)
            lcd.text("No config.py", 5, 80, WHITE)
        lcd.show()
        utime.sleep(2)
        machine.reset()


# ---------- Config helpers ----------

def url_decode(s):
    s = s.replace("+", " ")
    out = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i+1:i+3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out += s[i]
        i += 1
    return out


def parse_query(query):
    params = {}
    if not query:
        return params
    for pair in query.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = url_decode(v)
    return params


def write_config_py(ssid, pwd, ns_url, token, endpoint,
                    units, stale_min, low_thr, high_thr,
                    alert_du, alert_dd):
    def esc(x):
        return x.replace('"', '\\"')

    units_str = "mmol" if units == "mmol" else "mgdl"

    src = (
        'WIFI_SSID = "{ssid}"\n'
        'WIFI_PASSWORD = "{pwd}"\n'
        'NS_URL = "{ns_url}"\n'
        'NS_TOKEN = "{token}"\n'
        'API_ENDPOINT = "{endpoint}"\n'
        'DISPLAY_UNITS = "{units}"\n'
        'STALE_MIN = {stale}\n'
        'LOW_THRESHOLD = {low}\n'
        'HIGH_THRESHOLD = {high}\n'
        'ALERT_DOUBLE_UP = {alert_du}\n'
        'ALERT_DOUBLE_DOWN = {alert_dd}\n'
    ).format(
        ssid=esc(ssid),
        pwd=esc(pwd),
        ns_url=esc(ns_url),
        token=esc(token),
        endpoint=esc(endpoint),
        units=units_str,
        stale=stale_min,
        low=low_thr,
        high=high_thr,
        alert_du="True" if alert_du else "False",
        alert_dd="True" if alert_dd else "False",
    )

    print("Writing config.py with:")
    print(src)

    with open("config.py", "w") as f:
        f.write(src)

    try:
        st = os.stat("config.py")
        print("config.py written OK, stat:", st)
    except OSError as e:
        print("Failed to stat config.py:", e)


# ---------- Config portal HTML ----------

CONFIG_FORM_HTML = """HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Device Configuration Portal</title>
    
    <style>
        :root {
            --primary-color: #005A9C;
            --primary-hover: #00457A;
            --accent-color: #1A936F;
            --text-color: #333333;
            --light-bg: #f7f9fc;
            --border-color: #e0e6ed;
            --help-text-color: #7f8c8d;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--light-bg);
            padding: 20px;
            display: flex;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            color: var(--text-color);
        }
        .professional-form {
            max-width: 650px;
            width: 100%;
            background: #ffffff;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            border-top: 6px solid var(--primary-color);
        }
        .form-header {
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
        }
        .form-header h1 {
            color: var(--primary-color);
            font-size: 2em;
            font-weight: 700;
            margin: 0;
        }
        .form-header p {
            color: var(--help-text-color);
            margin-top: 10px;
            font-size: 0.95em;
        }
        fieldset {
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 30px;
            transition: border-color 0.3s;
        }
        fieldset:hover {
            border-color: #cccccc;
        }
        legend {
            font-size: 1.2em;
            font-weight: 600;
            color: var(--primary-color);
            padding: 0 10px;
        }
        .form-group {
            margin-bottom: 18px;
        }
        .form-group label {
            display: block;
            margin-bottom: 6px;
            color: var(--text-color);
            font-weight: 500;
            font-size: 0.95em;
        }
        .form-group label.required::after {
            content: '*';
            color: var(--primary-color);
            margin-left: 4px;
        }
        .form-group input[type="text"],
        .form-group input[type="password"],
        .form-group input[type="url"],
        .form-group input[type="number"],
        .form-group select {
            width: 100%;
            padding: 12px;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            box-sizing: border-box;
            font-size: 1em;
            transition: border-color 0.3s, box-shadow 0.3s;
        }
        .form-group input:focus,
        .form-group select:focus {
            border-color: var(--accent-color);
            box-shadow: 0 0 0 3px rgba(26, 147, 111, 0.15);
            outline: none;
        }
        .help-text {
            display: block;
            font-size: 0.8em;
            color: var(--help-text-color);
            margin-top: 5px;
        }
        .checkbox-group {
            display: flex;
            align-items: center;
            margin-bottom: 10px;
        }
        .checkbox-group input[type="checkbox"] {
            margin-right: 10px;
            transform: scale(1.1);
            cursor: pointer;
            accent-color: var(--primary-color);
        }
        .checkbox-group label {
            font-weight: normal;
            color: var(--text-color);
            cursor: pointer;
        }
        .form-actions {
            padding-top: 20px;
            text-align: right;
        }
        .submit-button {
            background-color: var(--primary-color);
            color: white;
            padding: 14px 30px;
            border: none;
            border-radius: 8px;
            font-size: 1.05em;
            font-weight: 600;
            cursor: pointer;
            transition: background-color 0.3s, transform 0.1s;
            box-shadow: 0 4px 10px rgba(0, 90, 156, 0.3);
        }
        .submit-button:hover {
            background-color: var(--primary-hover);
            box-shadow: 0 6px 15px rgba(0, 90, 156, 0.4);
        }
        .submit-button:active {
            transform: translateY(1px);
        }
    </style>
</head>
<body>

<form action="/save" method="GET" class="professional-form">
  
  <div class="form-header">
    <h1>Device Configuration Portal</h1>
  </div>
  
  <fieldset>
    <legend>📡 Wi-Fi Setup</legend>
    <div class="form-group">
      <label for="ssid" class="required">Wi-Fi SSID (Network Name)</label>
      <input type="text" id="ssid" name="ssid" placeholder="HomeNetwork" required>
    </div>
    <div class="form-group">
      <label for="pwd" class="required">Wi-Fi Password</label>
      <input type="password" id="pwd" name="pwd" placeholder="Enter secure password" required>
    </div>
  </fieldset>

  <fieldset>
    <legend>☁️ Nightscout Integration</legend>
    <div class="form-group">
      <label for="ns_url" class="required">Nightscout URL</label>
      <input type="url" id="ns_url" name="ns_url" placeholder="https://your-site.herokuapp.com" required>
      <span class="help-text">Use the full URL starting with "https://"</span>
    </div>
    <div class="form-group">
      <label for="token" class="required">API Secret (Access Token)</label>
      <input type="text" id="token" name="token" placeholder="e.g., your-api-secret" required>
      <span class="help-text">This is your Nightscout connection security key.</span>
    </div>
    <div class="form-group">
      <label for="endpoint">API Endpoint</label>
      <input type="text" id="endpoint" name="endpoint" value="/api/v1/entries/sgv.json?count=2">
      <span class="help-text">Generally, this default value does not need to be changed.</span>
    </div>
  </fieldset>

  <fieldset>
    <legend>📈 Display &amp; Alert Settings</legend>
    
    <div class="form-group">
      <label for="units">Glucose Measurement Units</label>
      <select id="units" name="units">
        <option value="mmol">mmol/L</option>
        <option value="mgdl">mg/dL</option>
      </select>
    </div>

    <div class="form-group">
      <label for="stale">Data Stale Threshold (minutes)</label>
      <input type="number" id="stale" name="stale" value="7" min="1" max="60" required>
      <span class="help-text">Sets a warning if data has not been updated within this time.</span>
    </div>
    <div class="form-group">
      <label for="low">Low Blood Glucose Alert Threshold</label>
      <input type="number" id="low" name="low" value="4.0" step="0.1" required>
    </div>
    <div class="form-group">
      <label for="high">High Blood Glucose Alert Threshold</label>
      <input type="number" id="high" name="high" value="11.0" step="0.1" required>
    </div>
  </fieldset>

  <fieldset>
    <legend>🚨 Trend Alert Options</legend>
    <div class="checkbox-group">
      <input type="checkbox" id="alert_du" name="alert_du" checked="checked">
      <label for="alert_du">Enable urgent alert for Double Up trend</label>
    </div>
    <div class="checkbox-group">
      <input type="checkbox" id="alert_dd" name="alert_dd" checked="checked">
      <label for="alert_dd">Enable urgent alert for Double Down trend</label>
    </div>
  </fieldset>

  <div class="form-actions">
    <button type="submit" class="submit-button">Save Configuration &amp; Reboot</button>
  </div>
</form>

</body>
</html>
"""

CONFIG_SAVED_HTML = """HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Configuration Saved - Success</title>
    <style>
        :root {
            --primary-color: #005A9C;
            --accent-color: #1A936F;
            --text-color: #333333;
            --light-bg: #f7f9fc;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--light-bg);
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            color: var(--text-color);
            text-align: center;
        }
        .confirmation-card {
            max-width: 450px;
            width: 100%;
            background: #ffffff;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
            border-top: 6px solid var(--accent-color);
        }
        .success-icon {
            font-size: 4em;
            color: var(--accent-color);
            margin-bottom: 20px;
            line-height: 1;
        }
        .confirmation-card h1 {
            font-size: 2em;
            font-weight: 700;
            margin-top: 0;
            margin-bottom: 10px;
        }
        .confirmation-card p {
            color: #555555;
            font-size: 1.1em;
            margin-bottom: 25px;
            line-height: 1.5;
        }
        .status-message {
            display: inline-block;
            background-color: #e6f7f2;
            color: var(--accent-color);
            padding: 10px 20px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.95em;
        }
    </style>
</head>
<body>
     <div class="confirmation-card">
    <div class="success-icon">
        ✓
    </div>
    <h1>Configuration Saved Successfully!</h1>
    <p>Your new settings have been saved.</p>
    <div class="status-message">
      The device will now apply your settings and start using your Nightscout data.
    </div>
    </div>
</body>
</html>
"""


def handle_config_save_from_query(query, lcd):
    params = parse_query(query)

    ssid     = params.get("ssid", "").strip()
    pwd      = params.get("pwd", "").strip()
    ns_url   = params.get("ns_url", "").strip()
    token    = params.get("token", "").strip()
    endpoint = params.get("endpoint", "").strip() or "/api/v1/entries/sgv.json?count=2"

    units    = params.get("units", "mmol").strip().lower()
    if units not in ("mmol", "mgdl"):
        units = "mmol"

    def to_float(name, default):
        v = params.get(name, "").strip()
        if not v:
            return default
        try:
            return float(v)
        except ValueError:
            return default

    stale_min = to_float("stale", 7.0)
    low_thr   = to_float("low", 4.0)
    high_thr  = to_float("high", 11.0)

    alert_du = "alert_du" in params
    alert_dd = "alert_dd" in params

    print("Form values:", ssid, ns_url, units, stale_min, low_thr, high_thr, alert_du, alert_dd)

    if not (ssid and ns_url):
        print("Missing required fields, redisplaying form")
        return False, CONFIG_FORM_HTML

    lcd.fill(BLACK)
    lcd.text("Saving config", 5, 60, WHITE)
    lcd.show()

    write_config_py(
        ssid, pwd, ns_url, token, endpoint,
        units, stale_min, low_thr, high_thr,
        alert_du, alert_dd
    )

    return True, CONFIG_SAVED_HTML


# ---------- HTTP send helper ----------

def send_http_response(cl, html):
    """
    Reliably send a full HTTP response string in chunks.
    """
    if isinstance(html, str):
        html = html.encode("utf-8")

    mv = memoryview(html)
    total = 0
    length = len(mv)

    while total < length:
        sent = cl.send(mv[total:total+512])
        if sent is None:
            break
        total += sent


# ---------- AP config portal ----------

def ap_config_portal(lcd):
    lcd.fill(BLACK)
    lcd.text("AP Setup Mode", 5, 30, WHITE)
    lcd.text("SSID: NSDisplay", 5, 50, WHITE)
    lcd.text("Pass: nsdisplay", 5, 70, WHITE)
    lcd.text("Go: 192.168.4.1", 5, 90, WHITE)
    lcd.show()

    # Turn off STA when in config mode
    sta = network.WLAN(network.STA_IF)
    sta.active(False)

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="NSDisplay", password="nsdisplay")

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    print("Config portal on 192.168.4.1:80")

    while True:
        cl, addr = s.accept()
        print("Config request from", addr)
        req = cl.recv(1024)
        if not req:
            cl.close()
            continue

        req_str = req.decode("utf-8", "ignore")
        first_line = req_str.split("\r\n", 1)[0]
        parts = first_line.split()
        if len(parts) < 2:
            send_http_response(cl, CONFIG_FORM_HTML)
            cl.close()
            continue

        method, path = parts[0], parts[1]

        if path.startswith("/save?"):
            query = path.split("?", 1)[1]
            ok, resp_html = handle_config_save_from_query(query, lcd)
            send_http_response(cl, resp_html)
            cl.close()
            if ok:
                # Give browser a moment to receive the response
                utime.sleep(2)
                print("Config saved, performing full reset")
                machine.reset()   # same as pressing RESET
                # no return needed; reset never returns
        else:
            send_http_response(cl, CONFIG_FORM_HTML)
            cl.close()



# ---------- Wi-Fi + NTP ----------

def connect_wifi(lcd):
    lcd.fill(BLACK)
    lcd.text("Collecting berries", 5, 60, WHITE)
    lcd.show()

    # Make sure AP is off in normal run mode
    ap = network.WLAN(network.AP_IF)
    ap.active(False)

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    max_wait = 5
    dots_x = 8
    while max_wait > 0:
        status = wlan.status()
        if status < 0 or status >= 3:
            break
        lcd.text(".", dots_x, 70, WHITE)
        lcd.show()
        dots_x += 6
        utime.sleep(1)
        max_wait -= 1

    if wlan.status() != 3:
        lcd.fill(BLACK)
        lcd.text("Wi-Fi FAILED", 5, 40, WHITE)
        lcd.text("Status: {}".format(wlan.status()), 5, 60, WHITE)
        lcd.text("Retrying...", 5, 80, WHITE)
        lcd.show()
        raise RuntimeError("Network connection failed (status {})".format(wlan.status()))

    ip = wlan.ifconfig()[0]
    print("Connected. IP:", ip)

    try:
        ntptime.settime()
        print("Time synchronized.")
    except Exception as e:
        print("NTP error:", e)
        lcd.fill(BLACK)
        lcd.text("Time sync error", 5, 50, WHITE)
        lcd.text("Retrying...", 5, 70, WHITE)
        lcd.show()
        utime.sleep(2)

    lcd.fill(BLACK)
    lcd.text("Wi-Fi OK", 5, 40, WHITE)
    lcd.text("IP:", 5, 60, WHITE)
    lcd.text(ip, 5, 80, WHITE)
    lcd.show()
    utime.sleep(2)


# ---------- Nightscout fetch ----------

def fetch_glucose_data():
    headers = {}
    if NS_TOKEN:
        headers["api-secret"] = NS_TOKEN

    full_url = NS_URL + API_ENDPOINT
    print("Fetching:", full_url)
    try:
        resp = requests.get(full_url, headers=headers)
        status = getattr(resp, "status_code", getattr(resp, "status", "unknown"))
        print("HTTP status:", status)
        data = resp.json()
        resp.close()
        return data
    except OSError as e:
        # Network-level error (TCP, route, etc.)
        print("HTTP network error in fetch_glucose_data:", repr(e))
        try:
            resp.close()
        except Exception:
            pass
        return None
    except Exception as e:
        # JSON parse or other unexpected error
        print("HTTP/JSON error in fetch_glucose_data:", repr(e))
        try:
            resp.close()
        except Exception:
            pass
        return None



# ---------- Parse JSON ----------

def parse_and_calculate(data):
    global DISPLAY_UNITS

    if not data:
        print("parse_and_calculate: data is empty or None")
        return None

    if not isinstance(data, list):
        print("parse_and_calculate: expected list, got", type(data))
        return None

    if len(data) < 1:
        print("parse_and_calculate: list length < 1")
        return None

    if "sgv" not in data[0]:
        print('parse_and_calculate: "sgv" not in first element')
        return None

    current_bg_mgdl = data[0]["sgv"]
    current_time_ms = data[0]["date"]
    trend_direction = data[0].get("direction", "NONE")

    if DISPLAY_UNITS == "mgdl":
        current_val = float(current_bg_mgdl)
    else:
        current_val = round(current_bg_mgdl / 18, 1)

    delta_val = None
    if len(data) > 1 and "sgv" in data[1]:
        prev_bg_mgdl = data[1]["sgv"]
        delta_mgdl = current_bg_mgdl - prev_bg_mgdl
        if DISPLAY_UNITS == "mgdl":
            delta_val = float(delta_mgdl)
        else:
            delta_val = round(delta_mgdl / 18, 1)

    arrow = {
        "Flat": "A ",
        "SingleUp": "C  ",
        "DoubleUp": "CC ",
        "TripleUp": "CCC ",
        "SingleDown": "D ",
        "DoubleDown": "DD ",
        "TripleDown": "DDD ",
        "FortyFiveUp": "G ",
        "FortyFiveDown": "H ",
        "NOT COMPUTABLE": "-- ",
        "NONE": "-- ",
    }.get(trend_direction, "?")

    return {
        "bg_val": current_val,
        "delta": delta_val,
        "arrow": arrow,
        "raw_time_ms": current_time_ms,
        "direction": trend_direction,
    }


# ---------- Determine alert colour ----------

def get_alert_color(bg_data, minutes):
    global STALE_MIN, LOW_THRESHOLD, HIGH_THRESHOLD
    global ALERT_DOUBLE_UP, ALERT_DOUBLE_DOWN

    bg_val    = bg_data["bg_val"]
    direction = bg_data["direction"]

    stale       = minutes >= STALE_MIN
    low         = bg_val < LOW_THRESHOLD
    double_up   = ALERT_DOUBLE_UP   and direction == "DoubleUp"
    double_down = ALERT_DOUBLE_DOWN and direction == "DoubleDown"
    high        = bg_val > HIGH_THRESHOLD

    if stale or low or double_up or double_down:
        return RED

    if high:
        return YELLOW
    return WHITE


# ---------- Drawing ----------

def draw_screen(lcd, bg_data):
    global DISPLAY_UNITS

    lcd.fill(BLACK)

    if not bg_data:
        lcd.text("NO DATA", 5, 50, WHITE)
        lcd.text("Retrying...", 5, 70, WHITE)
        lcd.show()
        return

    BOTTOM_ROW_H = max(SMALL_H, ARROWS)
    total_height = SMALL_H + BG_H + BOTTOM_ROW_H + 2 * LINE_GAP

    y_offset = (DISPLAY_HEIGHT - total_height) // 2

    y_time  = y_offset
    y_bg    = y_time  + SMALL_H + LINE_GAP
    y_delta = y_bg    + BG_H    + LINE_GAP

    raw_time_s = bg_data["raw_time_ms"] // 1000
    minutes = int((utime.time() - raw_time_s) // 60)
    if minutes < 0:
        minutes = 0
    time_str = "{} mins ago".format(minutes)

    alert_color = get_alert_color(bg_data, minutes)

    CWriter.set_textpos(lcd, y_time, center_x(wri_small, time_str))
    wri_small.setcolor(alert_color, BLACK)
    wri_small.printstring(time_str)

    bg_val = bg_data["bg_val"]
    if DISPLAY_UNITS == "mgdl":
        bg_text = str(int(round(bg_val)))
    else:
        bg_text = "{:.1f}".format(bg_val)

    CWriter.set_textpos(lcd, y_bg, center_x(wri_bg, bg_text))
    wri_bg.setcolor(alert_color, BLACK)
    wri_bg.printstring(bg_text)

    arrow_text = bg_data["arrow"]
    delta_val = bg_data["delta"]

    if delta_val is None:
        delta_text = "N/A"
    else:
        if DISPLAY_UNITS == "mgdl":
            delta_text = "{:+.0f}".format(delta_val)
        else:
            delta_text = "{:+.1f}".format(delta_val)

    half = DISPLAY_WIDTH // 2
    arrow_w = wri_arrows.stringlen(arrow_text)
    delta_w = wri_small.stringlen(delta_text)

    x_arrow = (half - arrow_w) // 2
    x_delta = half + (half - delta_w) // 2

    delta_v_offset = (ARROWS - SMALL_H) // 2

    CWriter.set_textpos(lcd, y_delta, x_arrow)
    wri_arrows.setcolor(alert_color, BLACK)
    wri_arrows.printstring(arrow_text)

    CWriter.set_textpos(lcd, y_delta + delta_v_offset, x_delta)
    wri_small.setcolor(alert_color, BLACK)
    wri_small.printstring(delta_text)

    lcd.show()


# ---------- Main ----------

def main():
    global DISPLAY_WIDTH, DISPLAY_HEIGHT
    global wri_small, wri_bg, wri_arrows
    global WIFI_SSID, WIFI_PASSWORD, NS_URL, NS_TOKEN, API_ENDPOINT
    global DISPLAY_UNITS, STALE_MIN, LOW_THRESHOLD, HIGH_THRESHOLD
    global ALERT_DOUBLE_UP, ALERT_DOUBLE_DOWN
    global user_config, HAS_CONFIG

    print(":) booting")

    lcd = ST7735()
    lcd.fill(BLACK)
    lcd.show()

    DISPLAY_WIDTH  = lcd.width
    DISPLAY_HEIGHT = lcd.height

    check_factory_reset(lcd)

    if not HAS_CONFIG:
        print("No valid config.py found, starting AP config portal")
        ap_config_portal(lcd)

        # After portal returns, try to load the new config
        try:
            import config as cfg  # fresh import after writing file
            user_config = cfg
            if hasattr(user_config, "WIFI_SSID") and hasattr(user_config, "NS_URL"):
                HAS_CONFIG = True
            else:
                HAS_CONFIG = False
        except ImportError:
            user_config = None
            HAS_CONFIG = False

        if not HAS_CONFIG:
            lcd.fill(BLACK)
            lcd.text("Config error", 5, 40, WHITE)
            lcd.text("Rebooting...", 5, 60, WHITE)
            lcd.show()
            utime.sleep(3)
            machine.reset()
            return

    # Load config values
    WIFI_SSID     = getattr(user_config, "WIFI_SSID", "")
    WIFI_PASSWORD = getattr(user_config, "WIFI_PASSWORD", "")
    NS_URL        = getattr(user_config, "NS_URL", "")
    NS_TOKEN      = getattr(user_config, "NS_TOKEN", "")
    API_ENDPOINT  = getattr(user_config, "API_ENDPOINT", "/api/v1/entries/sgv.json?count=2")

    DISPLAY_UNITS = getattr(user_config, "DISPLAY_UNITS", "mmol")
    if DISPLAY_UNITS not in ("mmol", "mgdl"):
        DISPLAY_UNITS = "mmol"

    STALE_MIN      = float(getattr(user_config, "STALE_MIN", 7.0))
    LOW_THRESHOLD  = float(getattr(user_config, "LOW_THRESHOLD", 4.0))
    HIGH_THRESHOLD = float(getattr(user_config, "HIGH_THRESHOLD", 11.0))

    ALERT_DOUBLE_UP   = bool(getattr(user_config, "ALERT_DOUBLE_UP", True))
    ALERT_DOUBLE_DOWN = bool(getattr(user_config, "ALERT_DOUBLE_DOWN", True))

    print("Using units:", DISPLAY_UNITS)
    print("Stale >=", STALE_MIN, "Low <", LOW_THRESHOLD, "High >", HIGH_THRESHOLD)
    print("Alert DoubleUp:", ALERT_DOUBLE_UP, "DoubleDown:", ALERT_DOUBLE_DOWN)

    wri_small  = CWriter(lcd, font_main,   fgcolor=WHITE, bgcolor=BLACK)
    wri_bg     = CWriter(lcd, font_bg,     fgcolor=WHITE, bgcolor=BLACK)
    wri_arrows = CWriter(lcd, font_arrows, fgcolor=WHITE, bgcolor=BLACK)

    # Wi-Fi boot retry; if totally broken, reboot (no re-config unless factory reset)
    wifi_fail = 0
    while True:
        try:
            connect_wifi(lcd)
            wifi_fail = 0
            break
        except Exception as e:
            wifi_fail += 1
            print("Wi-Fi/NTP error during boot (attempt {}):".format(wifi_fail), e)
            utime.sleep(5)
            if wifi_fail >= 3:
                print("Too many Wi-Fi failures, rebooting")
                lcd.fill(BLACK)
                lcd.text("Wi-Fi FAILED", 5, 40, WHITE)
                lcd.text("Rebooting...", 5, 60, WHITE)
                lcd.show()
                utime.sleep(3)
                machine.reset()
                return

    # Post-connect wait to avoid first EHOSTUNREACH
    print("Post-connect wait to stabilize Wi-Fi...")
    for _ in range(3):
        utime.sleep(1)
        heartbeat("wifi-wait")

    processed_data = None
    sync_counter = 0

    while True:
        if sync_counter >= SYNC_INTERVAL:
            try:
                ntptime.settime()
                print("Time re-synced.")
            except Exception as e:
                print("NTP re-sync error:", e)
            sync_counter = 0

        sync_counter += 1

        print("\n--- Fetching new data ---")
        try:
            raw = fetch_glucose_data()
        except OSError as e:
            print("Network error in main loop:", e)
            try:
                connect_wifi(lcd)
            except Exception as e2:
                print("Re-connect failed:", e2)
            raw = None
        except Exception as e:
            print("Other error in main loop:", e)
            raw = None

        new_data = None
        if raw is not None:
            new_data = parse_and_calculate(raw)

        if new_data:
            processed_data = new_data
            print("Data fetch OK.")
        else:
            print("Fetch failed; using last data.")

        if processed_data:
            draw_screen(lcd, processed_data)
        else:
            draw_screen(lcd, None)

        heartbeat("main-loop")
        utime.sleep(5)


if __name__ == "__main__":
    main()

