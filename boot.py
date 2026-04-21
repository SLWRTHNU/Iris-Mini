# boot.py - Iris Mini ESP32-S3
# Runs on every boot, before main.py
from machine import Pin
p = Pin(38, Pin.OUT, value=1)
print("Iris Mini - booting")
# Factory reset is handled at runtime via task_factory_reset_button in app_main.py

