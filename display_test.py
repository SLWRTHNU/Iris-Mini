# display_test.py
# Standalone layout test — no WiFi or server needed.
# Flash to the device and run directly to see the display positioned
# exactly as it appears in the live app.

import gc
from display_2inch import lcd_st7789 as LCD_Driver
from writer import CWriter
import large_font
import small_font      as font_small
import age_small_font  as age_font_small
import arrows_font     as font_arrows
import heart           as font_heart
import delta           as font_delta

# ---------- Colors (must match app_main.py) ----------
YELLOW = 0xFFE0
RED    = 0xF800
GREEN  = 0x07E0
BLACK  = 0x0000
WHITE  = 0xFFFF

# ---------- Mock data ----------
AGE_TEXT    = "88 mins ago"
AGE_COLOR   = WHITE
BG_TEXT     = "88.8"
BG_COLOR    = GREEN
ARROW_TEXT  = "OO"        # double-up
ARROW_COLOR = YELLOW      # double-up → yellow in app_main
DELTA_TEXT  = "+8.8"      # sign drawn with delta font, number with small font
HEART_ON    = True

# ---------- Main ----------
def run():
    gc.collect()

    fb  = bytearray(320 * 240 * 2)
    lcd = LCD_Driver(fb=fb, bl=80)

    # Writers — spacing must match app_main.py
    w_large      = CWriter(lcd, large_font,    fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_small      = CWriter(lcd, font_small,    fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_age        = CWriter(lcd, age_font_small, fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_arrow      = CWriter(lcd, font_arrows,   fgcolor=WHITE, bgcolor=BLACK, verbose=False)
    w_heart      = CWriter(lcd, font_heart,    fgcolor=RED,   bgcolor=BLACK, verbose=False)
    w_delta_icon = CWriter(lcd, font_delta,    fgcolor=WHITE, bgcolor=BLACK, verbose=False)

    w_large.set_spacing(2)
    w_small.set_spacing(3)
    w_age.set_spacing(2)
    w_arrow.set_spacing(8)

    # ---------- Layout (mirrors draw_all_fields_if_needed) ----------
    W, H = lcd.width, lcd.height

    y_age = 0

    heart_h = w_heart.font.height()
    heart_w = w_heart.stringlen("T")
    age_h   = w_age.font.height()
    x_heart = W - 10 - heart_w
    y_heart = y_age + (age_h - heart_h) // 4

    big_h   = w_large.font.height()
    small_h = w_small.font.height()
    arrow_h = w_arrow.font.height()

    y_bg = (H - big_h) // 2

    x_arrow = 0
    y_arrow = H - arrow_h   # flush bottom-left
    y_delta = H - small_h   # flush bottom-right

    # ---------- Draw ----------
    lcd.fill(BLACK)

    # Age — centred at top
    age_w = w_age.stringlen(AGE_TEXT)
    w_age.setcolor(AGE_COLOR, BLACK)
    w_age.set_textpos(lcd, y_age, (W - age_w) // 2)
    w_age.printstring(AGE_TEXT)

    # Heart — top-right alongside age
    if HEART_ON:
        w_heart.setcolor(RED, BLACK)
        w_heart.set_textpos(lcd, y_heart, x_heart)
        w_heart.printstring("T")

    # BG — centred vertically
    bg_w = w_large.stringlen(BG_TEXT)
    w_large.setcolor(BG_COLOR, BLACK)
    w_large.set_textpos(lcd, y_bg, (W - bg_w) // 2)
    w_large.printstring(BG_TEXT)

    # Arrow — bottom-left
    w_arrow.setcolor(ARROW_COLOR, BLACK)
    w_arrow.set_textpos(lcd, y_arrow, x_arrow)
    w_arrow.printstring(ARROW_TEXT)

    # Delta icon + number — bottom-right
    gap          = 12   # pixels between icon and number
    v_offset     = 0    # vertical offset of icon relative to number
    NUM_Y_OFFSET = 0    # vertical nudge on the number
    NUM_X_OFFSET = 0    # horizontal nudge on the number
    right_margin = 0    # gap from right edge

    sign    = DELTA_TEXT[0]    # "+"
    val_num = DELTA_TEXT[1:]   # "8.8"

    h_delta = w_delta_icon.font.height()
    y_icon  = y_delta + (small_h - h_delta) // 2 + v_offset

    num_w  = w_small.stringlen(val_num)
    sign_w = w_delta_icon.stringlen(sign)
    x_num  = W - right_margin - num_w
    x_sign = x_num - sign_w - gap

    w_delta_icon.setcolor(WHITE, BLACK)
    w_delta_icon.set_textpos(lcd, y_icon, x_sign)
    w_delta_icon.printstring(sign)

    w_small.setcolor(WHITE, BLACK)
    w_small.set_textpos(lcd, y_delta + NUM_Y_OFFSET, x_num + NUM_X_OFFSET)
    w_small.printstring(val_num)

    # Flush full frame
    lcd.show()
    print("Layout test rendered.")
    print("  Age:   y={}, x={}".format(y_age, (W - age_w) // 2))
    print("  Heart: y={}, x={}".format(y_heart, x_heart))
    print("  BG:    y={}, x={}".format(y_bg, (W - bg_w) // 2))
    print("  Arrow: y={}, x={}  (font h={})".format(y_arrow, x_arrow, arrow_h))
    print("  Delta: y={}, x_sign={}, x_num={}  (font h={})".format(y_delta, x_sign, x_num, small_h))

run()
