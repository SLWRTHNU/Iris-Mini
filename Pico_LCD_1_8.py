from machine import Pin, SPI, PWM
import framebuf
import time

BL = 13
DC = 8
RST = 12
MOSI = 11
SCK = 10
CS = 9

class Palette(framebuf.FrameBuffer):
    def __init__(self):
        buf = bytearray(4)  # 2 pixels * 2 bytes
        super().__init__(buf, 2, 1, framebuf.RGB565)

    def bg(self, color):
        self.pixel(0, 0, color)

    def fg(self, color):
        self.pixel(1, 0, color)

class LCD_1inch8(framebuf.FrameBuffer):
    def __init__(self):
        self.width = 160
        self.height = 128
        
        self.cs = Pin(CS, Pin.OUT)
        self.rst = Pin(RST, Pin.OUT)
        self.dc = Pin(DC, Pin.OUT)
        
        self.cs(1)
        self.dc(1)
        
        # Reduced speed to 4MHz for better stability on 1.8" ribbons
        self.spi = SPI(1, 4_000_000, polarity=0, phase=0, sck=Pin(SCK), mosi=Pin(MOSI), miso=None)
        
        self.buffer = bytearray(self.height * self.width * 2)
        super().__init__(self.buffer, self.width, self.height, framebuf.RGB565)

        self.palette = Palette()
        self.init_display()
        
        # Standard colors
        self.WHITE = 0xFFFF
        self.BLACK = 0x0000
        self.RED   = 0x00F8 
        self.GREEN = 0x07E0
        self.BLUE  = 0x001F

    def write_cmd(self, cmd):
        self.dc(0)
        self.cs(0)
        self.spi.write(bytearray([cmd]))
        self.cs(1)

    def write_data(self, buf):
        self.dc(1)
        self.cs(0)
        if isinstance(buf, int):
            self.spi.write(bytearray([buf]))
        else:
            self.spi.write(buf)
        self.cs(1)

    def init_display(self):
        self.rst(1)
        time.sleep_ms(5)
        self.rst(0)
        time.sleep_ms(5)
        self.rst(1)
        time.sleep_ms(5)
        
        # 0x36 MADCTL: Controls orientation and Color Order
        # 0x70 was your original. 
        # 0x78 or 0x68 usually swaps Red/Blue while keeping Landscape
        self.write_cmd(0x36)
        self.write_data(0x78) 

        self.write_cmd(0x3A) # Interface Pixel Format
        self.write_data(0x05) # 16-bit/pixel

        # ST7735R Frame Rate
        self.write_cmd(0xB1); self.write_data(0x01); self.write_data(0x2C); self.write_data(0x2D)
        self.write_cmd(0xB2); self.write_data(0x01); self.write_data(0x2C); self.write_data(0x2D)
        self.write_cmd(0xB3); self.write_data(0x01); self.write_data(0x2C); self.write_data(0x2D)
        self.write_data(0x01); self.write_data(0x2C); self.write_data(0x2D)

        self.write_cmd(0xB4); self.write_data(0x07) # Column inversion

        # Power Sequence
        self.write_cmd(0xC0); self.write_data(0xA2); self.write_data(0x02); self.write_data(0x84)
        self.write_cmd(0xC1); self.write_data(0xC5)
        self.write_cmd(0xC2); self.write_data(0x0A); self.write_data(0x00)
        self.write_cmd(0xC3); self.write_data(0x8A); self.write_data(0x2A)
        self.write_cmd(0xC4); self.write_data(0x8A); self.write_data(0xEE)
        self.write_cmd(0xC5); self.write_data(0x0E)

        # Gamma
        self.write_cmd(0xe0)
        self.write_data(bytearray([0x0f,0x1a,0x0f,0x18,0x2f,0x28,0x20,0x22,0x1f,0x1b,0x23,0x37,0x00,0x07,0x02,0x10]))
        self.write_cmd(0xe1)
        self.write_data(bytearray([0x0f,0x1b,0x0f,0x17,0x33,0x2c,0x29,0x2e,0x30,0x30,0x39,0x3f,0x00,0x07,0x03,0x10]))

        self.write_cmd(0x11) # Sleep out
        time.sleep_ms(120)
        self.write_cmd(0x29) # Display on

    def show(self):
        # These are the most common offsets for the 1.8" Red/Black tab screens
        X_OFFSET = 1  
        Y_OFFSET = 2  

        # Column Address Set (X)
        self.write_cmd(0x2A)
        self.write_data(0x00)
        self.write_data(X_OFFSET)               # Start X
        self.write_data(0x00)
        self.write_data(X_OFFSET + 160 - 1)     # End X (Exactly 160 pixels wide)

        # Row Address Set (Y)
        self.write_cmd(0x2B)
        self.write_data(0x00)
        self.write_data(Y_OFFSET)               # Start Y
        self.write_data(0x00)
        self.write_data(Y_OFFSET + 128 - 1)     # End Y (Exactly 128 pixels high)
        
        self.write_cmd(0x2C) # Memory Write
        
        self.cs(1)
        self.dc(1)
        self.cs(0)
        self.spi.write(self.buffer)
        self.cs(1)
        
    def draw_scaled_text(self, text, x, y, color, scale=2):
        import framebuf
        # Create a tiny 1-bit mask of the text
        w = 8 * len(text)
        h = 8
        buf = bytearray((w * h) // 8)
        fb = framebuf.FrameBuffer(buf, w, h, framebuf.MONO_HLSB)
        
        fb.fill(0) # Background of mask is 0
        fb.text(text, 0, 0, 1) # Text in mask is 1
        
        for yy in range(h):
            for xx in range(w):
                # We check the mask: if the pixel is NOT 1, we do NOTHING.
                # This is what prevents the 'box' from appearing.
                if fb.pixel(xx, yy) == 1:
                    # Manually draw a block of pixels for the scale
                    for sy in range(scale):
                        for sx in range(scale):
                            # Use the base pixel method to skip all framebuf background logic
                            self.pixel(x + (xx * scale) + sx, y + (yy * scale) + sy, color)
