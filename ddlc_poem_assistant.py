import ctypes
import json
import os
import re
import shutil
import sys
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox


def _set_windows_dpi_awareness():
    """make windows use the real screen dpi"""
    if sys.platform != "win32":
        return
    try:
        ctypes.OleDLL("shcore").SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        # fallback for older windows
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


_set_windows_dpi_awareness()

# optional packages
try:
    import mss
except ImportError:
    mss = None

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:
    Image = None
    ImageDraw = None
    
    ImageTk = None
try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    from rapidfuzz import process, fuzz
except ImportError:
    process = None
    fuzz = None

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # move the mouse to a corner to stop
    pyautogui.PAUSE = 0
except ImportError:
    pyautogui = None

try:
    import keyboard  # optional f12 stop hotkey
except ImportError:
    keyboard = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else BASE_DIR
)
WORD_DATA_PATH = os.path.join(RESOURCE_DIR, "word_data.json")
REGION_CONFIG_PATH = os.path.join(APP_DIR, "capture_region.json")
WINDOWS_INSTALLER_PATH = os.path.join(APP_DIR, "install_windows.bat")
APP_SETTINGS_PATH = os.path.join(APP_DIR, "app_settings.json")

CHARACTERS = ["sayori", "natsuki", "yuri", "monika"]

CHARACTER_COLORS = {
    "sayori": "#ff9dc0",
    "natsuki": "#ff6e78",
    "yuri": "#9b6fc9",
    "monika": "#b8cf5a",
}


def lighten_color(hex_color, factor=0.6):
    """make a color lighter"""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def darken_color(hex_color, factor=0.15):
    """make a color darker"""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r, g, b = int(r * (1 - factor)), int(g * (1 - factor)), int(b * (1 - factor))
    return f"#{r:02x}{g:02x}{b:02x}"


CHARACTER_TINTS = {char: lighten_color(color, 0.65) for char, color in CHARACTER_COLORS.items()}

CHARACTER_LABELS = {
    "sayori": "Sayori",
    "natsuki": "Natsuki",
    "yuri": "Yuri",
    "monika": "Monika",
}

PALETTE = {
    "bg": "#fdf1f6",
    "card": "#fffbfd",
    "card_border": "#f6d9e6",
    "header_from": "#ffb3d1",
    "header_to": "#ffe3ee",
    "text": "#4a2e3d",
    "text_muted": "#a18290",
    "accent": "#ff6f9c",
    "accent_hover": "#ff4d84",
    "accent_soft": "#ffe1ec",
    "setup": "#b48ad1",
    "setup_hover": "#9d6fbd",
    "autoplay": "#7cc49a",
    "autoplay_hover": "#5fae80",
    "stop": "#e0607e",
    "stop_hover": "#c94a68",
    "best_highlight": "#ffedb0",
    "row_alt": "#fff5f9",
    "unknown_text": "#b096a0",
}

FONT = "Aller"

FONT_FALLBACKS = ["Aller", "Segoe UI", "SF Pro Text", "Helvetica Neue",
                   "Ubuntu", "Noto Sans", "DejaVu Sans", "Arial"]

ALLER_AVAILABLE = False


def resolve_font(root):
    """use aller if it's installed, otherwise use a fallback font"""
    global FONT, ALLER_AVAILABLE
    try:
        available = set(tkfont.families(root))
    except Exception:
        return
    ALLER_AVAILABLE = "Aller" in available
    for name in FONT_FALLBACKS:
        if name in available:
            FONT = name
            return


FUZZY_MATCH_THRESHOLD = 78

# auto-play settings
OCR_SCALE_FACTOR = 2
AUTOPLAY_CLICK_DELAY = 1.3
AUTOPLAY_MAX_ROUNDS = 20
AUTOPLAY_END_STREAK = 2
AUTOPLAY_MIN_KNOWN_WORDS = 2
AUTOPLAY_STOP_HOTKEY = "f12"

UI_SUPERSAMPLE = 4


def load_app_settings():
    """load saved settings if there are any"""
    try:
        with open(APP_SETTINGS_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_app_settings(settings):
    """save the settings next to the app"""
    try:
        with open(APP_SETTINGS_PATH, "w", encoding="utf-8") as file:
            json.dump(settings, file, indent=2)
    except OSError:
        pass


class WordDatabase:
    """word data and lookups"""

    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.meta = data.get("meta", {})
        # normalized word -> data
        self.entries = {}
        for word, points in data["words"].items():
            key = self._normalize(word)
            self.entries[key] = {
                "display": word.capitalize() if "-" not in word else word.title(),
                "points": points,
                "key": key,
            }
        self._keys = list(self.entries.keys())
        self._word_set = set(self._keys)

        # these are the act 2 words that transfer from sayori
        raw_transfers = data.get("act2_sayori_transfers", {})
        self.act2_transfers = {
            char: {self._normalize(w) for w in words}
            for char, words in raw_transfers.items()
        }

    @staticmethod
    def _normalize(text):
        text = text.lower().strip()
        text = text.replace("-", "").replace(" ", "").replace("'", "")
        text = re.sub(r"[^a-z0-9]", "", text)
        return text

    def lookup(self, raw_token):
        """look up one ocr token"""
        key = self._normalize(raw_token)
        if not key:
            return None, 0
        if key in self.entries:
            return self.entries[key], 100
        if process is None:
            return None, 0
        match = process.extractOne(key, self._keys, scorer=fuzz.ratio)
        if match and match[1] >= FUZZY_MATCH_THRESHOLD:
            matched_key = match[0]
            return self.entries[matched_key], match[1]
        return None, 0

    def points_for(self, entry, character):
        if character == "monika":
            return 3
        return entry["points"].get(character, 0)

    def is_act2_inherited(self, entry, character):
        """check if this is a transferred act 2 word"""
        return entry["key"] in self.act2_transfers.get(character, set())


def preprocess_image(pil_img):
    """prep the screenshot for ocr"""
    img = pil_img.convert("L")
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    return img


def split_merged_tokens(token, word_set):
    """try to split one ocr token into known words"""
    if ' ' in token:
        space_split = token.lower().split()
        all_known = True
        for word in space_split:
            normalized = word.replace("-", "").replace("'", "")
            normalized = re.sub(r"[^a-z0-9]", "", normalized)
            if normalized not in word_set:
                all_known = False
                break
        if all_known and len(space_split) > 1:
            return space_split
    
    token_lower = token.lower().replace(" ", "")
    results = []
    
    def find_words(s, current_split):
        if not s:
            results.append(current_split[:])
            return
        for i in range(min(len(s), 20), 0, -1):
            candidate = s[:i]
            if candidate in word_set:
                current_split.append(candidate)
                find_words(s[i:], current_split)
                current_split.pop()
    
    find_words(token_lower, [])
    
    if results:
        best_split = max(results, key=len)
        if len(best_split) > 1:
            return best_split
    
    return [token]


def extract_words_from_image(pil_img):
    """run the simple ocr pass"""
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract is not installed, or the Tesseract program isn't on "
            "your PATH. See README.md for setup instructions."
        )
    processed = preprocess_image(pil_img)
    raw_text = pytesseract.image_to_string(processed, config="--psm 6")
    tokens = re.split(r"[\n\r]+|\s{2,}|\s(?=[A-Z])", raw_text)
    tokens = [t.strip(" .,:;!?\"'()[]") for t in tokens]
    tokens = [t for t in tokens if len(t) >= 2 and any(c.isalpha() for c in t)]
    return tokens


def extract_words_with_boxes(pil_img):
    """run ocr and keep the word boxes for preview and auto-play"""
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract is not installed, or the Tesseract program isn't on "
            "your PATH. See README.md for setup instructions."
        )
    processed = preprocess_image(pil_img)
    data = pytesseract.image_to_data(
        processed, config="--psm 11", output_type=pytesseract.Output.DICT
    )
    boxes = []
    n = len(data.get("text", []))
    for i in range(n):
        text = data["text"][i].strip(" .,:;!?\"'()[]")
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if len(text) >= 2 and any(c.isalpha() for c in text) and conf >= 30:
            boxes.append({
                "text": text,
                "left": data["left"][i] / OCR_SCALE_FACTOR,
                "top": data["top"][i] / OCR_SCALE_FACTOR,
                "width": data["width"][i] / OCR_SCALE_FACTOR,
                "height": data["height"][i] / OCR_SCALE_FACTOR,
            })
    return boxes


def grab_region(region):
    """take a screenshot of the saved region"""
    if mss is None:
        raise RuntimeError("The 'mss' package is not installed. See README.md.")
    left, top, width, height = region
    with mss.mss() as sct:
        monitor = {"left": left, "top": top, "width": width, "height": height}
        shot = sct.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def draw_gradient(canvas, width, height, color1, color2):
    """draw a gradient"""
    r1, g1, b1 = canvas.winfo_rgb(color1)
    r2, g2, b2 = canvas.winfo_rgb(color2)
    width = max(int(width), 1)
    r_step = (r2 - r1) / width
    g_step = (g2 - g1) / width
    b_step = (b2 - b1) / width
    for i in range(width):
        nr = int(r1 + r_step * i) >> 8
        ng = int(g1 + g_step * i) >> 8
        nb = int(b1 + b_step * i) >> 8
        canvas.create_line(i, 0, i, height, fill=f"#{nr:02x}{ng:02x}{nb:02x}")


def draw_scalloped_edge(canvas, width, y, scallop_r, color):
    """draw the little scalloped edge"""
    step = scallop_r * 2
    x = -scallop_r
    while x < width + scallop_r:
        canvas.create_oval(
            x - scallop_r, y - scallop_r, x + scallop_r, y + scallop_r,
            fill=color, outline=color,
        )
        x += step


def draw_polka_dots(canvas, width, height, dot_color, radius=3, spacing=24, y_start=0):
    """draw the background dots"""
    row = 0
    y = y_start + spacing / 2
    while y < height:
        x_offset = (spacing / 2) if row % 2 else 0
        x = x_offset
        while x < width:
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                fill=dot_color, outline="")
            x += spacing
        y += spacing * 0.87
        row += 1


def _rounded_rect_points(x1, y1, x2, y2, r):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    return [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]


def _hex_to_rgb(hex_color):
    value = hex_color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _lanczos_filter():
    """get the pillow resize filter"""
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def _make_rounded_image(width, height, fill, outline=None, radius=0, outline_width=1):
    """make a smooth rounded shape"""
    if Image is None or ImageDraw is None or ImageTk is None:
        return None
    try:
        width = max(int(width), 1)
        height = max(int(height), 1)
        scale = UI_SUPERSAMPLE
        image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        box = [
            1 * scale,
            1 * scale,
            max(width - 1, 1) * scale,
            max(height - 1, 1) * scale,
        ]
        draw.rounded_rectangle(
            box,
            radius=max(int(radius * scale), 1),
            fill=fill,
            outline=outline or fill,
            width=max(int(outline_width * scale), 1),
        )
        return image.resize((width, height), _lanczos_filter())
    except Exception:
        return None


def _make_decorative_strip(width, height, color1, color2, dot_color=None,
                           radius=0, spacing=24, scallop_color=None,
                           scallop_radius=0, y_start=0):
    """make one of the pink decorative strips"""
    if Image is None or ImageDraw is None or ImageTk is None:
        return None
    try:
        width = max(int(width), 1)
        height = max(int(height), 1)
        scale = UI_SUPERSAMPLE
        scaled_width = width * scale
        scaled_height = height * scale
        image = Image.new("RGBA", (scaled_width, scaled_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        rgb1 = _hex_to_rgb(color1)
        rgb2 = _hex_to_rgb(color2)
        denominator = max(scaled_width - 1, 1)
        for x in range(scaled_width):
            factor = x / denominator
            color = tuple(int(rgb1[i] + (rgb2[i] - rgb1[i]) * factor) for i in range(3))
            draw.line((x, 0, x, scaled_height), fill=color + (255,))

        if dot_color:
            row = 0
            y = (y_start + spacing / 2) * scale
            while y < scaled_height:
                x_offset = (spacing / 2) if row % 2 else 0
                x = x_offset * scale
                while x < scaled_width:
                    r = radius * scale
                    draw.ellipse((x - r, y - r, x + r, y + r), fill=dot_color)
                    x += spacing * scale
                y += spacing * 0.87 * scale
                row += 1

        if scallop_color and scallop_radius > 0:
            r = scallop_radius * scale
            step = r * 2
            x = -r
            y = height * scale
            while x < scaled_width + r:
                draw.ellipse((x - r, y - r, x + r, y + r), fill=scallop_color)
                x += step

        return image.resize((width, height), _lanczos_filter())
    except Exception:
        return None


class RoundedButton(tk.Canvas):
    """a rounded button made with a canvas"""

    def __init__(self, parent, text, command=None, bg=None, fg="white",
                 hover_bg=None, selected_bg=None, selected_fg=None, font=None,
                 radius=16, padx=18, pady=10, min_width=0, parent_bg=None, state="normal"):
        self.font_obj = tkfont.Font(font=font or (FONT, 10, "bold"))
        text_w = self.font_obj.measure(text)
        text_h = self.font_obj.metrics("linespace")
        w = max(text_w + padx * 2, min_width)
        h = text_h + pady * 2
        bgcolor = parent_bg
        if bgcolor is None:
            try:
                bgcolor = parent.cget("bg")
            except Exception:
                bgcolor = PALETTE["bg"]
        super().__init__(parent, width=w, height=h, bg=bgcolor,
                          highlightthickness=0, bd=0)
        self.command = command
        self.text = text
        self.bg = bg or PALETTE["accent"]
        self.hover_bg = hover_bg or self.bg
        self.selected_bg = selected_bg or self.hover_bg
        self.fg = fg
        self.selected_fg = selected_fg or self.fg
        self.radius = radius
        self.padx = padx
        self.state = state
        self.selected = False
        self._hovering = False
        self._shape_photo = None
        self._redraw()
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            w = int(self["width"])
        if h <= 1:
            h = int(self["height"])
        if self.state == "disabled":
            color, textcolor = self.bg, self.fg
        elif self.selected:
            color, textcolor = self.selected_bg, self.selected_fg
        elif self._hovering:
            color, textcolor = self.hover_bg, self.fg
        else:
            color, textcolor = self.bg, self.fg
        pts = _rounded_rect_points(2, 2, w - 2, h - 2, self.radius)
        smooth_shape = _make_rounded_image(
            w,
            h,
            color,
            darken_color(color, 0.25) if self.selected else color,
            self.radius,
            2 if self.selected else 1,
        )
        if smooth_shape is not None:
            self._shape_photo = ImageTk.PhotoImage(smooth_shape)
            self.create_image(0, 0, anchor="nw", image=self._shape_photo, tags="shape")
        else:
            self.create_polygon(pts, smooth=True, fill=color, outline=color)
            if self.selected:
                self.create_polygon(pts, smooth=True, fill="", outline=darken_color(color, 0.25), width=2)
        self.create_text(w // 2, h // 2, text=self.text, fill=textcolor, font=self.font_obj)

    def _on_enter(self, event=None):
        if self.state == "disabled":
            return
        self._hovering = True
        self.configure(cursor="hand2")
        self._redraw()

    def _on_leave(self, event=None):
        self._hovering = False
        self.configure(cursor="arrow")
        self._redraw()

    def _on_click(self, event=None):
        if self.state != "disabled" and self.command:
            self.command()

    def config_style(self, bg=None, fg=None, text=None, state=None, hover_bg=None,
                      selected_bg=None, selected_fg=None, selected=None, font=None):
        """change the button without rebuilding it"""
        if bg is not None:
            self.bg = bg
        if fg is not None:
            self.fg = fg
        if hover_bg is not None:
            self.hover_bg = hover_bg
        if selected_bg is not None:
            self.selected_bg = selected_bg
        if selected_fg is not None:
            self.selected_fg = selected_fg
        if selected is not None:
            self.selected = selected
        if font is not None:
            self.font_obj = tkfont.Font(font=font)
        if text is not None:
            self.text = text
            new_w = self.font_obj.measure(text) + self.padx * 2
            cur_w = self.winfo_width()
            if cur_w <= 1:
                cur_w = int(self["width"])
            if new_w > cur_w:
                self.configure(width=new_w)
        if state is not None:
            self.state = state
        self._redraw()


class RoundedCard(tk.Frame):
    """a rounded frame for the different sections"""

    def __init__(self, parent, bg=None, border=None, radius=14, parent_bg=None):
        self.bg = bg or PALETTE["card"]
        self.border = border or PALETTE["card_border"]
        self.radius = radius
        resolved_parent_bg = parent_bg
        if resolved_parent_bg is None:
            try:
                resolved_parent_bg = parent.cget("bg")
            except Exception:
                resolved_parent_bg = PALETTE["bg"]
        super().__init__(parent, bg=resolved_parent_bg)
        self.canvas = tk.Canvas(self, bg=resolved_parent_bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=self.bg)
        self._win = self.canvas.create_window(4, 4, window=self.body, anchor="nw")
        self._bg_photo = None
        self.body.bind("<Configure>", self._sync_min_size)
        self.canvas.bind("<Configure>", self._redraw)

    def _sync_min_size(self, event=None):
        w = self.body.winfo_reqwidth() + 8
        h = self.body.winfo_reqheight() + 8
        self.canvas.configure(width=w, height=h)
        self._redraw()

    def _redraw(self, event=None):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1:
            w = int(self.canvas["width"])
        if h <= 1:
            h = int(self.canvas["height"])
        self.canvas.delete("bgshape")
        if w > 4 and h > 4:
            pts = _rounded_rect_points(1, 1, w - 1, h - 1, self.radius)
            smooth_shape = _make_rounded_image(
                w, h, self.bg, self.border, self.radius, 1
            )
            if smooth_shape is not None:
                self._bg_photo = ImageTk.PhotoImage(smooth_shape)
                self.canvas.create_image(
                    0, 0, anchor="nw", image=self._bg_photo, tags="bgshape"
                )
            else:
                self.canvas.create_polygon(pts, smooth=True, fill=self.bg,
                                            outline=self.border, width=1, tags="bgshape")
            self.canvas.tag_lower("bgshape")
            self.canvas.coords(self._win, 4, 4)
            self.canvas.itemconfig(self._win, width=max(w - 8, 1), height=max(h - 8, 1))


class RegionSelector(tk.Toplevel):
    """the fullscreen box selector"""

    def __init__(self, master, on_selected):
        super().__init__(master)
        self.on_selected = on_selected

        if mss is not None:
            with mss.mss() as sct:
                mon = sct.monitors[0]
                self.origin = (mon["left"], mon["top"])
                geo = f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}"
        else:
            self.origin = (0, 0)
            geo = f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0"

        self.geometry(geo)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.30)
        self.configure(bg="black", cursor="crosshair")

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            20, 20, anchor="nw", fill="white",
            font=("Segoe UI", 14, "bold"),
            text="Drag a box around the poem word-choice area, then release.\n"
                 "The assistant is hidden while you select. Keep it outside\n"
                 "this box while scanning so it cannot cover any words.\n"
                 "Press Esc to cancel.",
        )

        self.start = None
        self.rect_id = None
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda e: self.destroy())

    def _on_press(self, event):
        self.start = (event.x, event.y)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ffea00", width=2
        )

    def _on_drag(self, event):
        if self.rect_id is not None:
            x0, y0 = self.start
            self.canvas.coords(self.rect_id, x0, y0, event.x, event.y)

    def _on_release(self, event):
        if self.start is None:
            self.destroy()
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        ox, oy = self.origin
        region = (int(left + ox), int(top + oy), int(right - left), int(bottom - top))
        self.destroy()
        if region[2] > 5 and region[3] > 5:
            self.on_selected(region)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DDLC+ Poem Word Assistant")
        self.settings = load_app_settings()
        saved_geometry = self.settings.get("window_geometry")
        if isinstance(saved_geometry, str) and re.fullmatch(
                r"\d+x\d+(?:[+-]\d+){0,2}", saved_geometry):
            self.geometry(saved_geometry)
        else:
            self.geometry("520x780")
        self.minsize(480, 660)
        self.attributes("-topmost", True)
        self.configure(bg=PALETTE["bg"])

        try:
            self.db = WordDatabase(WORD_DATA_PATH)
        except Exception as e:
            messagebox.showerror("Data error", f"Couldn't load word_data.json:\n{e}")
            self.destroy()
            return

        self.region = self._load_region()
        saved_character = self.settings.get("selected_character", "sayori")
        saved_character = saved_character if saved_character in CHARACTERS else "sayori"
        self.current_act = self.settings.get("current_act", "1")
        self.current_act = self.current_act if self.current_act in ("1", "2") else "1"
        if self.current_act == "2" and saved_character == "sayori":
            saved_character = "yuri"
        self.selected_character = tk.StringVar(value=saved_character)
        self.last_results = []

        self.autoplay_active = False
        self.autoplay_stop_requested = False
        try:
            self.click_delay = float(self.settings.get("click_delay", AUTOPLAY_CLICK_DELAY))
        except (TypeError, ValueError):
            self.click_delay = AUTOPLAY_CLICK_DELAY
        self.click_delay = round(max(0.3, min(5.0, self.click_delay)), 1)

        self.style = ttk.Style()
        self.style.theme_use("clam")

        self.style.configure("Treeview",
            background=PALETTE["card"],
            foreground=PALETTE["text"],
            rowheight=28,
            fieldbackground=PALETTE["card"],
            borderwidth=0,
            font=(FONT, 10)
        )
        self.style.configure("Treeview.Heading",
            background=PALETTE["accent_soft"],
            foreground=PALETTE["text"],
            relief="flat",
            font=(FONT, 10, "bold")
        )
        self.style.map("Treeview.Heading",
            background=[("active", PALETTE["accent_soft"])]
        )
        self.style.configure("Vertical.TScrollbar",
            background=PALETTE["accent_soft"],
            troughcolor=PALETTE["bg"],
            borderwidth=0,
            arrowsize=12
        )

        resolve_font(self)
        self._set_window_icon()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _save_preferences(self):
        self.settings.update({
            "selected_character": self.selected_character.get(),
            "current_act": self.current_act,
            "click_delay": self.click_delay,
            "window_geometry": self.geometry(),
        })
        save_app_settings(self.settings)

    def _on_close(self):
        self._save_preferences()
        self.destroy()

    def _load_region(self):
        if os.path.exists(REGION_CONFIG_PATH):
            try:
                with open(REGION_CONFIG_PATH) as f:
                    r = json.load(f)
                    return (r["left"], r["top"], r["width"], r["height"])
            except Exception:
                return None
        return None

    def _save_region(self, region):
        with open(REGION_CONFIG_PATH, "w") as f:
            json.dump(
                {"left": region[0], "top": region[1],
                 "width": region[2], "height": region[3]}, f
            )

    def _set_window_icon(self):
        """make the little heart icon"""
        if Image is None:
            return
        try:
            from PIL import ImageDraw, ImageTk
            size = 32
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([2, 5, 18, 21], fill=PALETTE["accent"])
            draw.ellipse([14, 5, 30, 21], fill=PALETTE["accent"])
            draw.polygon([(3, 15), (29, 15), (16, 30)], fill=PALETTE["accent"])
            self._icon_image = ImageTk.PhotoImage(img)
            self.iconphoto(True, self._icon_image)
        except Exception:
            pass

    def _build_ui(self):
        header = tk.Canvas(self, height=64, highlightthickness=0, bd=0)
        header.pack(fill="x")

        def _paint_header(event=None):
            header.delete("all")
            w = header.winfo_width()
            if w <= 1:
                w = 520
            smooth_header = _make_decorative_strip(
                w,
                64,
                PALETTE["header_from"],
                PALETTE["header_to"],
                dot_color=lighten_color(PALETTE["header_from"], 0.4),
                radius=3,
                spacing=26,
                scallop_color=PALETTE["bg"],
                scallop_radius=7,
            )
            if smooth_header is not None:
                header._background_photo = ImageTk.PhotoImage(smooth_header)
                header.create_image(0, 0, anchor="nw", image=header._background_photo)
            else:
                draw_gradient(header, w, 64, PALETTE["header_from"], PALETTE["header_to"])
                draw_polka_dots(header, w, 64, lighten_color(PALETTE["header_from"], 0.4),
                                 radius=3, spacing=26)
                draw_scalloped_edge(header, w, 64, 7, PALETTE["bg"])
            header.create_text(
                18, 20, anchor="w", text="🎀  DDLC+ Poem Assistant",
                font=(FONT, 15, "bold"), fill=PALETTE["text"],
            )
            header.create_text(
                18, 42, anchor="w",
                text="Find the best word for each doki, straight off your screen.",
                font=(FONT, 8), fill=PALETTE["text"],
            )
        header.bind("<Configure>", _paint_header)
        header.update_idletasks()
        _paint_header()

        footer = tk.Canvas(self, height=14, highlightthickness=0, bd=0, bg=PALETTE["accent_soft"])
        footer.pack(fill="x", side="bottom")

        def _paint_footer(event=None):
            footer.delete("all")
            w = footer.winfo_width()
            if w <= 1:
                w = 520
            smooth_footer = _make_decorative_strip(
                w,
                14,
                PALETTE["accent_soft"],
                PALETTE["accent_soft"],
                dot_color=lighten_color(PALETTE["accent"], 0.55),
                radius=2,
                spacing=20,
            )
            if smooth_footer is not None:
                footer._background_photo = ImageTk.PhotoImage(smooth_footer)
                footer.create_image(0, 0, anchor="nw", image=footer._background_photo)
            else:
                draw_polka_dots(footer, w, 14, lighten_color(PALETTE["accent"], 0.55),
                                 radius=2, spacing=20)
        footer.bind("<Configure>", _paint_footer)
        footer.update_idletasks()
        _paint_footer()

        content = tk.Frame(self, bg=PALETTE["bg"])
        content.pack(fill="both", expand=True, padx=12, pady=12)

        if not ALLER_AVAILABLE:
            tk.Label(
                content,
                text=f"This UI is designed for the 'Aller' font (DDLC's actual "
                     f"font) - currently showing in {FONT} instead. Install the "
                     f"included Aller_Std_Rg.ttf for the authentic look.",
                font=(FONT, 8), bg=PALETTE["accent_soft"], fg=PALETTE["text"],
                wraplength=480, justify="left", padx=10, pady=6,
            ).pack(fill="x", pady=(0, 10))

        setup_card = RoundedCard(content)
        setup_card.pack(fill="x", pady=(0, 12))
        setup_frame = setup_card.body
        setup_row = tk.Frame(setup_frame, bg=PALETTE["card"])
        setup_row.pack(fill="x", padx=12, pady=10)

        self.setup_status_var = tk.StringVar()
        tk.Label(
            setup_row, textvariable=self.setup_status_var, font=(FONT, 8),
            bg=PALETTE["card"], fg=PALETTE["text"], justify="left",
            anchor="w", wraplength=235,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        tesseract_path_btn = RoundedButton(
            setup_row, text="Tesseract Path", command=self._choose_tesseract_path,
            font=(FONT, 8, "bold"), bg=PALETTE["accent_soft"],
            fg=PALETTE["text"], hover_bg=PALETTE["card_border"],
            radius=12, padx=9, pady=5,
        )
        tesseract_path_btn.pack(side="right", padx=(4, 0))

        repair_btn = RoundedButton(
            setup_row, text="Repair Setup", command=self._repair_setup,
            font=(FONT, 8, "bold"), bg=PALETTE["setup"], fg="white",
            hover_bg=PALETTE["setup_hover"], radius=12, padx=9, pady=5,
        )
        repair_btn.pack(side="right", padx=(4, 0))
        self._update_setup_status()

        act_card = RoundedCard(content)
        act_card.pack(fill="x", pady=(0, 12))
        act_frame = act_card.body

        act_label = tk.Label(
            act_frame, text="Which act are you in?", font=(FONT, 11, "bold"),
            bg=PALETTE["card"], fg=PALETTE["text"]
        )
        act_label.pack(anchor="w", padx=12, pady=(12, 4))

        act_button_row = tk.Frame(act_frame, bg=PALETTE["card"])
        act_button_row.pack(fill="x", padx=8, pady=(0, 4))

        self.act_buttons = {}
        for act_id, act_text in [("1", "Act 1"), ("2", "Act 2 (Sayori gone)")]:
            abtn = RoundedButton(
                act_button_row, text=act_text, font=(FONT, 10, "bold"),
                bg=PALETTE["accent_soft"], fg=PALETTE["text"],
                hover_bg=PALETTE["card_border"],
                selected_bg=PALETTE["accent"], selected_fg="white",
                radius=14,
                command=lambda a=act_id: self._select_act(a),
            )
            abtn.pack(side="left", expand=True, fill="x", padx=4)
            self.act_buttons[act_id] = abtn

        tk.Label(
            act_frame,
            text="Act 2: Sayori's Act-1 words become tile options for Natsuki/Yuri "
                 "too (their point values don't change - Sayori just can't be picked anymore).",
            font=(FONT, 8), bg=PALETTE["card"], fg=PALETTE["text_muted"],
            wraplength=460, justify="left", anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 12))

        self._update_act_buttons()

        char_card = RoundedCard(content)
        char_card.pack(fill="x", pady=(0, 12))
        char_frame = char_card.body

        char_label = tk.Label(
            char_frame, text="Optimize for:", font=(FONT, 11, "bold"),
            bg=PALETTE["card"], fg=PALETTE["text"]
        )
        char_label.pack(anchor="w", padx=12, pady=(12, 4))

        button_container = tk.Frame(char_frame, bg=PALETTE["card"])
        button_container.pack(fill="x", padx=8, pady=(0, 12))

        self.char_buttons = {}
        for char in CHARACTERS:
            btn = RoundedButton(
                button_container,
                text=CHARACTER_LABELS[char],
                font=(FONT, 10, "bold"),
                bg=CHARACTER_TINTS[char],
                fg=PALETTE["text"],
                hover_bg=CHARACTER_COLORS[char],
                radius=16,
                command=lambda c=char: self._select_character(c)
            )
            btn.pack(side="left", expand=True, fill="x", padx=4)
            self.char_buttons[char] = btn

        self._update_character_buttons()
        self._update_character_availability()

        action_card = RoundedCard(content)
        action_card.pack(fill="x", pady=(0, 12))
        action_frame = action_card.body

        action_inner = tk.Frame(action_frame, bg=PALETTE["card"])
        action_inner.pack(fill="x", padx=12, pady=(12, 8))

        self.scan_btn = RoundedButton(
            action_inner,
            text="Scan Screen",
            command=self._scan_clicked,
            font=(FONT, 12, "bold"),
            bg=PALETTE["accent"],
            fg="white",
            hover_bg=PALETTE["accent_hover"],
            radius=18,
            min_width=180,
        )
        self.scan_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))

        region_btn = RoundedButton(
            action_inner,
            text="Setup",
            command=self._select_region,
            font=(FONT, 10, "bold"),
            bg=PALETTE["setup"],
            fg="white",
            hover_bg=PALETTE["setup_hover"],
            radius=18,
        )
        region_btn.pack(side="left", padx=(6, 0))

        self.autoplay_btn = RoundedButton(
            action_inner,
            text="Auto-play",
            command=self._autoplay_clicked,
            font=(FONT, 10, "bold"),
            bg=PALETTE["autoplay"],
            fg="white",
            hover_bg=PALETTE["autoplay_hover"],
            radius=18,
            min_width=140,
        )
        self.autoplay_btn.pack(side="left", padx=(6, 0))

        preview_row = tk.Frame(action_frame, bg=PALETTE["card"])
        preview_row.pack(fill="x", padx=12, pady=(0, 8))
        self.preview_btn = RoundedButton(
            preview_row, text="Preview OCR Capture", command=self._preview_ocr_clicked,
            font=(FONT, 9, "bold"), bg=PALETTE["accent_soft"],
            fg=PALETTE["text"], hover_bg=PALETTE["card_border"],
            radius=14, padx=12, pady=6,
        )
        self.preview_btn.pack(side="left")
        tk.Label(
            preview_row, text="See the capture and detected words before scanning.",
            font=(FONT, 8), bg=PALETTE["card"], fg=PALETTE["text_muted"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

        delay_row = tk.Frame(action_frame, bg=PALETTE["card"])
        delay_row.pack(fill="x", padx=12, pady=(0, 2))

        tk.Label(
            delay_row, text="Auto-play delay:", font=(FONT, 9, "bold"),
            bg=PALETTE["card"], fg=PALETTE["text"]
        ).pack(side="left")

        minus_btn = RoundedButton(
            delay_row, text="–", font=(FONT, 10, "bold"),
            bg=PALETTE["accent_soft"], fg=PALETTE["text"],
            hover_bg=PALETTE["card_border"], radius=10, padx=10, pady=4,
            command=lambda: self._adjust_delay(-0.1)
        )
        minus_btn.pack(side="left", padx=(8, 2))

        self.delay_value_label = tk.Label(
            delay_row, text=f"{self.click_delay:.1f}s", font=(FONT, 9, "bold"),
            bg=PALETTE["card"], fg=PALETTE["accent"], width=5
        )
        self.delay_value_label.pack(side="left")

        plus_btn = RoundedButton(
            delay_row, text="+", font=(FONT, 10, "bold"),
            bg=PALETTE["accent_soft"], fg=PALETTE["text"],
            hover_bg=PALETTE["card_border"], radius=10, padx=10, pady=4,
            command=lambda: self._adjust_delay(0.1)
        )
        plus_btn.pack(side="left", padx=(2, 8))

        tk.Label(
            action_frame, text="(seconds between each auto-play scan/click)",
            font=(FONT, 8), bg=PALETTE["card"], fg=PALETTE["text_muted"],
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 12))

        self.status_var = tk.StringVar(value=self._region_status_text())
        status_label = tk.Label(
            content, textvariable=self.status_var, fg=PALETTE["text_muted"],
            bg=PALETTE["bg"], font=(FONT, 9), anchor="w"
        )
        status_label.pack(fill="x", pady=(0, 2))

        self.autoplay_status_var = tk.StringVar(value="")
        autoplay_status_label = tk.Label(
            content, textvariable=self.autoplay_status_var, fg=PALETTE["stop_hover"],
            bg=PALETTE["bg"], font=(FONT, 9, "bold"), anchor="w"
        )
        autoplay_status_label.pack(fill="x", pady=(0, 8))

        rec_card = RoundedCard(content, bg=PALETTE["best_highlight"], border=PALETTE["accent"])
        rec_card.pack(fill="x", pady=(0, 12))
        rec_frame = rec_card.body

        self.best_var = tk.StringVar(value="Scan the screen to see results.")
        best_label = tk.Label(
            rec_frame, textvariable=self.best_var, font=(FONT, 11, "bold"),
            wraplength=480, justify="left", bg=PALETTE["best_highlight"],
            fg=PALETTE["text"], padx=12, pady=12
        )
        best_label.pack(fill="x")

        results_card = RoundedCard(content)
        results_card.pack(fill="both", expand=True, pady=(0, 12))
        results_frame = results_card.body

        table_label = tk.Label(
            results_frame, text="Words Found", font=(FONT, 11, "bold"),
            bg=PALETTE["card"], fg=PALETTE["text"], anchor="w"
        )
        table_label.pack(fill="x", padx=12, pady=(12, 4))

        table_container = tk.Frame(results_frame, bg=PALETTE["card"])
        table_container.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        columns = ("word", "sayori", "natsuki", "yuri", "monika")
        self.tree = ttk.Treeview(
            table_container, columns=columns, show="headings", height=10
        )

        for col, label, width in [
            ("word", "Word", 140),
            ("sayori", "Sayori", 65),
            ("natsuki", "Natsuki", 65),
            ("yuri", "Yuri", 65),
            ("monika", "Monika", 65),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="center")
        self.tree.column("word", anchor="w")

        self.tree.tag_configure("best", background=PALETTE["best_highlight"], font=(FONT, 10, "bold"))
        self.tree.tag_configure("unknown", foreground=PALETTE["unknown_text"], font=(FONT, 10, "italic"))
        self.tree.tag_configure("even", background=PALETTE["row_alt"])
        self.tree.tag_configure("odd", background=PALETTE["card"])

        self.tree.pack(fill="both", expand=True, side="left")

        scroll = ttk.Scrollbar(table_container, command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        manual_card = RoundedCard(content)
        manual_card.pack(fill="x")
        manual_frame = manual_card.body

        manual_label = tk.Label(
            manual_frame, text="Manual Entry", font=(FONT, 10, "bold"),
            bg=PALETTE["card"], fg=PALETTE["text"], anchor="w"
        )
        manual_label.pack(fill="x", padx=12, pady=(12, 4))

        entry_container = tk.Frame(manual_frame, bg=PALETTE["card"])
        entry_container.pack(fill="x", padx=12, pady=(0, 12))

        self.manual_entry = tk.Entry(
            entry_container, font=(FONT, 10), relief="solid", bd=1,
            highlightthickness=1, highlightbackground=PALETTE["card_border"],
            highlightcolor=PALETTE["accent"]
        )
        self.manual_entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=6)
        self.manual_entry.bind("<Return>", lambda e: self._analyze_manual())

        manual_btn = RoundedButton(
            entry_container, text="Check", command=self._analyze_manual,
            font=(FONT, 10, "bold"), bg=PALETTE["accent"], fg="white",
            hover_bg=PALETTE["accent_hover"], radius=14,
        )
        manual_btn.pack(side="left")

    def _adjust_delay(self, step):
        new_val = round(max(0.3, min(5.0, self.click_delay + step)), 1)
        self.click_delay = new_val
        self.delay_value_label.config(text=f"{new_val:.1f}s")
        self._save_preferences()

    def _select_character(self, char):
        self.selected_character.set(char)
        self._update_character_buttons()
        self._rerender_results()
        self._save_preferences()

    def _update_character_buttons(self):
        selected = self.selected_character.get()
        for char, btn in self.char_buttons.items():
            if btn.state == "disabled":
                continue
            btn.config_style(selected=(char == selected))

    def _select_act(self, act_id):
        self.current_act = act_id
        self._update_act_buttons()
        self._update_character_availability()
        if act_id == "2" and self.selected_character.get() == "sayori":
            self._select_character("yuri")  # Sayori isn't a valid target once Act 2 starts
        self._rerender_results()
        self._save_preferences()

    def _update_act_buttons(self):
        for act_id, btn in self.act_buttons.items():
            btn.config_style(selected=(act_id == self.current_act))

    def _update_character_availability(self):
        sayori_btn = self.char_buttons["sayori"]
        if self.current_act == "2":
            sayori_btn.config_style(
                state="disabled", bg="#ecdfe4", fg="#b79aa6",
                text="Sayori (gone)", selected=False,
            )
        else:
            sayori_btn.config_style(
                state="normal", bg=CHARACTER_TINTS["sayori"], fg=PALETTE["text"],
                hover_bg=CHARACTER_COLORS["sayori"], selected_bg=CHARACTER_COLORS["sayori"],
                text=CHARACTER_LABELS["sayori"],
                selected=(self.selected_character.get() == "sayori"),
            )

    def _capture_region_overlaps_assistant(self):
        """check if the assistant covers part of the ocr area"""
        if not self.region or not self.winfo_viewable():
            return False
        try:
            self.update_idletasks()
            left, top, width, height = self.region
            region_right = left + width
            region_bottom = top + height
            app_left = self.winfo_rootx()
            app_top = self.winfo_rooty()
            app_right = app_left + self.winfo_width()
            app_bottom = app_top + self.winfo_height()
            return (
                left < app_right
                and app_left < region_right
                and top < app_bottom
                and app_top < region_bottom
            )
        except tk.TclError:
            return False

    def _capture_region_is_clear(self):
        if not self._capture_region_overlaps_assistant():
            return True
        messagebox.showwarning(
            "Capture region is covered",
            "The assistant window overlaps your saved poem-word capture region.\n\n"
            "Move the assistant completely outside the word area, then try again.\n\n"
            "OCR cannot recognize words hidden behind the assistant window.",
        )
        return False

    def _region_status_text(self):
        if self.region:
            return f"Capture region set: {self.region}"
        return "No capture region set yet - click 'Setup' first."

    def _update_setup_status(self):
        missing = missing_dependencies()
        region_state = "set" if self.region else "not set"
        tesseract_path = find_tesseract_executable()
        if missing:
            status = (
                "Setup needs attention: " + ", ".join(missing)
                + f"\nOCR: {'found' if tesseract_path else 'not found'} • "
                  f"Capture region: {region_state}"
            )
        else:
            status = (
                f"Setup ready • OCR: Tesseract found • Capture region: {region_state}"
            )
        if tesseract_path:
            status += "\nOCR not working? Use Repair Setup or Tesseract Path."
        else:
            status += "\nTesseract not found? Use Repair Setup or Tesseract Path."
        self.setup_status_var.set(status)

    def _choose_tesseract_path(self):
        current_path = find_tesseract_executable()
        initial_dir = os.path.dirname(current_path) if current_path else BASE_DIR
        selected_path = filedialog.askopenfilename(
            parent=self,
            title="Select tesseract.exe",
            initialdir=initial_dir,
            filetypes=[("Tesseract executable", "tesseract.exe"), ("Executables", "*.exe")],
        )
        if not selected_path:
            return

        try:
            check = subprocess.run(
                [selected_path, "--version"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            messagebox.showerror(
                "Invalid Tesseract path",
                f"That file could not be run as Tesseract:\n\n{exc}",
            )
            return
        if check.returncode != 0:
            messagebox.showerror(
                "Invalid Tesseract path",
                "That file did not respond to the Tesseract version check.",
            )
            return

        self.settings["tesseract_path"] = os.path.abspath(selected_path)
        self._save_preferences()
        configure_tesseract()
        self._update_setup_status()
        messagebox.showinfo("Tesseract configured", "Tesseract is ready for OCR.")

    def _repair_setup(self):
        proceed = messagebox.askokcancel(
            "Repair setup?",
            "This reruns the Windows setup installer to repair missing Python "
            "packages or Tesseract OCR.\n\n"
            "Installing Tesseract may require a UAC/admin prompt and may add "
            "Tesseract to your PATH for OCR to work.",
        )
        if proceed and run_windows_installer():
            self.destroy()

    def _preview_ocr_clicked(self):
        if not self.region:
            messagebox.showinfo(
                "No region set",
                "Click 'Setup' first and drag a box around the poem word area.",
            )
            return
        if not self._capture_region_is_clear():
            return
        if Image is None or ImageTk is None or pytesseract is None:
            messagebox.showerror(
                "OCR unavailable",
                "Install the missing setup components before previewing OCR.",
            )
            return

        self.preview_btn.config_style(state="disabled", text="Preparing Preview...")
        threading.Thread(target=self._prepare_ocr_preview, daemon=True).start()

    def _prepare_ocr_preview(self):
        try:
            screenshot = grab_region(self.region)
            boxes = extract_words_with_boxes(screenshot)
            preview = screenshot.copy()
            if ImageDraw is not None:
                draw = ImageDraw.Draw(preview)
                for box in boxes:
                    entry, _ = self.db.lookup(box["text"])
                    color = "#7cc49a" if entry else "#e0607e"
                    left, top = box["left"], box["top"]
                    right = left + box["width"]
                    bottom = top + box["height"]
                    draw.rectangle((left, top, right, bottom), outline=color, width=3)
            self.after(0, lambda: self._show_ocr_preview(preview, boxes))
        except Exception as exc:
            self.after(
                0,
                lambda: messagebox.showerror("OCR preview failed", str(exc)),
            )
        finally:
            self.after(
                0,
                lambda: self.preview_btn.config_style(
                    state="normal", text="Preview OCR Capture"
                ),
            )

    def _show_ocr_preview(self, image, boxes):
        preview_window = tk.Toplevel(self)
        preview_window.title("OCR Preview")
        preview_window.configure(bg=PALETTE["bg"])
        preview_window.transient(self)

        display_image = image.copy()
        display_image.thumbnail((820, 480), _lanczos_filter())
        preview_window._preview_photo = ImageTk.PhotoImage(display_image)
        tk.Label(
            preview_window, image=preview_window._preview_photo,
            bg=PALETTE["card"], bd=1, relief="solid",
        ).pack(padx=12, pady=(12, 8))

        recognized = []
        for box in boxes:
            entry, score = self.db.lookup(box["text"])
            label = entry["display"] if entry else f"{box['text']} (unknown)"
            if label not in recognized:
                recognized.append(label)
        summary = ", ".join(recognized) if recognized else "No text was recognized."
        tk.Label(
            preview_window,
            text=("Green boxes = recognized DDLC words • Red boxes = unknown\n"
                  f"OCR read: {summary}"),
            font=(FONT, 9), bg=PALETTE["bg"], fg=PALETTE["text"],
            wraplength=800, justify="left", anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 8))

        close_btn = RoundedButton(
            preview_window, text="Close", command=preview_window.destroy,
            font=(FONT, 9, "bold"), bg=PALETTE["accent"], fg="white",
            hover_bg=PALETTE["accent_hover"], radius=14, padx=16, pady=6,
            parent_bg=PALETTE["bg"],
        )
        close_btn.pack(pady=(0, 12))

    def _select_region(self):
        self.withdraw()
        self.after(150, self._open_region_selector)

    def _open_region_selector(self):
        def done(region):
            self.region = region
            self._save_region(region)
            self.status_var.set(self._region_status_text())
            self._update_setup_status()
            self.deiconify()

        selector = RegionSelector(self, done)
        selector.grab_set()
        self.wait_window(selector)
        self.deiconify()
        self._update_setup_status()

    def _scan_clicked(self):
        if not self.region:
            messagebox.showinfo(
                "No region set", "Click 'Select Region' first and drag a box "
                "around the row/grid of poem words in the game."
            )
            return
        if not self._capture_region_is_clear():
            return
        self.scan_btn.config_style(state="disabled", text="Scanning...")
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        try:
            img = grab_region(self.region)
            # same ocr as the preview
            boxes = extract_words_with_boxes(img)
            tokens = [box["text"] for box in boxes]
            results = []
            seen = set()
            
            for tok in tokens:
                # try exact/fuzzy lookup first
                entry, score = self.db.lookup(tok)
                
                if entry is None:
                    # maybe tesseract merged a few words together
                    split_words = split_merged_tokens(tok, self.db._word_set)
                    if len(split_words) > 1:
                        for word in split_words:
                            entry, score = self.db.lookup(word)
                            if entry:
                                key = entry["display"]
                                if key not in seen:
                                    seen.add(key)
                                    results.append((entry, word, score))
                    else:
                        # ignore random text outside the poem tiles
                        continue
                else:
                    key = entry["display"]
                    if key not in seen:
                        seen.add(key)
                        results.append((entry, tok, score))
                        
            self.last_results = results
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Scan failed", str(e)))
            self.last_results = []
        finally:
            self.after(0, self._scan_done)

    def _scan_done(self):
        self.scan_btn.config_style(state="normal", text="Scan Screen")
        self._rerender_results()

    def _autoplay_clicked(self):
        if self.autoplay_active:
            self._stop_autoplay()
            return

        if not self.region:
            messagebox.showerror(
                "No capture region set",
                "Please click 'Setup' and select the word-tile region first — "
                "auto-play reuses that same region."
            )
            return

        if not self._capture_region_is_clear():
            return

        if pyautogui is None:
            messagebox.showerror(
                "Missing dependency",
                "Auto-play needs the 'pyautogui' package.\n\npip install pyautogui"
            )
            return

        hotkey_line = (
            f"  • Press {AUTOPLAY_STOP_HOTKEY.upper()} (enabled)"
            if keyboard is not None else
            f"  • Press {AUTOPLAY_STOP_HOTKEY.upper()} "
            "(install the 'keyboard' package to enable this)"
        )
        proceed = messagebox.askokcancel(
            "Start auto-play?",
            "Auto-play will repeatedly click the best word for "
            f"{CHARACTER_LABELS[self.selected_character.get()]} inside your "
            "saved capture region.\n\n"
            "After you click OK you'll have 3 seconds to switch to the "
            "DDLC window.\n\n"
            "To stop at any time:\n"
            "  • Click this button again (it will say 'Stop Auto-play')\n"
            "  • Slam your mouse into a screen corner (instant panic-stop)\n"
            + hotkey_line
        )
        if not proceed:
            return

        self.autoplay_active = True
        self.autoplay_stop_requested = False
        self.autoplay_btn.config_style(text="Stop Auto-play", bg=PALETTE["stop"], hover_bg=PALETTE["stop_hover"])
        self.scan_btn.config_style(state="disabled")

        if keyboard is not None:
            try:
                keyboard.add_hotkey(AUTOPLAY_STOP_HOTKEY, self._request_autoplay_stop)
            except Exception:
                pass  # the mouse corner stop still works

        self._autoplay_countdown(3)

    def _autoplay_countdown(self, seconds_left):
        if self.autoplay_stop_requested:
            self._autoplay_finished("Stopped before starting.")
            return
        if seconds_left <= 0:
            self.autoplay_status_var.set("Auto-play running...")
            # get this on the main thread. tkinter vars aren't thread safe
            target_char = self.selected_character.get()
            threading.Thread(target=self._autoplay_loop, args=(target_char,), daemon=True).start()
            return
        self.autoplay_status_var.set(
            f"Switch to the DDLC window now — starting in {seconds_left}..."
        )
        self.after(1000, lambda: self._autoplay_countdown(seconds_left - 1))

    def _request_autoplay_stop(self):
        self.autoplay_stop_requested = True

    def _stop_autoplay(self):
        self._request_autoplay_stop()

    def _autoplay_loop(self, char):
        left, top, width, height = self.region
        previous_words = None
        low_word_streak = 0
        same_words_streak = 0
        round_num = 0
        end_reason = "Stopped."

        try:
            while round_num < AUTOPLAY_MAX_ROUNDS:
                if self.autoplay_stop_requested:
                    end_reason = "Stopped by you."
                    break

                try:
                    img = grab_region(self.region)
                    boxes = extract_words_with_boxes(img)
                except Exception as e:
                    end_reason = f"OCR error: {e}"
                    break

                candidates = []
                known_count = 0
                current_words = set()
                for box in boxes:
                    entry, score = self.db.lookup(box["text"])
                    if entry is None:
                        continue
                    known_count += 1
                    current_words.add(entry["display"])
                    pts = self.db.points_for(entry, char)
                    abs_x = left + box["left"] + box["width"] / 2
                    abs_y = top + box["top"] + box["height"] / 2
                    label = entry["display"]
                    if self.current_act == "2" and self.db.is_act2_inherited(entry, char):
                        label += " 🎀"
                    candidates.append((pts, label, abs_x, abs_y))

                low_word_streak = low_word_streak + 1 if known_count < AUTOPLAY_MIN_KNOWN_WORDS else 0

                if previous_words is not None and current_words and current_words == previous_words:
                    same_words_streak += 1
                else:
                    same_words_streak = 0
                previous_words = current_words

                if low_word_streak >= AUTOPLAY_END_STREAK:
                    end_reason = "Minigame appears to have ended (word tiles gone)."
                    break
                if same_words_streak >= AUTOPLAY_END_STREAK:
                    end_reason = "Stopped: same words detected repeatedly (click may not be registering)."
                    break
                if not candidates:
                    self.after(0, lambda r=round_num: self.autoplay_status_var.set(
                        f"Round {r + 1}: no recognizable words this scan, retrying..."))
                    time.sleep(self.click_delay)
                    round_num += 1
                    continue

                candidates.sort(key=lambda c: c[0], reverse=True)
                pts, word, x, y = candidates[0]

                self.after(0, lambda w=word, r=round_num: self.autoplay_status_var.set(
                    f"Round {r + 1}: clicking '{w}'..."))

                pyautogui.click(x, y)
                round_num += 1
                time.sleep(self.click_delay)
            else:
                end_reason = f"Stopped: reached the {AUTOPLAY_MAX_ROUNDS}-round safety limit."

        except pyautogui.FailSafeException:
            end_reason = "Panic-stopped (mouse moved to screen corner)."
        except Exception as e:
            end_reason = f"Auto-play error: {e}"
        finally:
            if keyboard is not None:
                try:
                    keyboard.remove_hotkey(AUTOPLAY_STOP_HOTKEY)
                except Exception:
                    pass
            self.after(0, lambda: self._autoplay_finished(end_reason))

    def _autoplay_finished(self, reason):
        self.autoplay_active = False
        self.autoplay_stop_requested = False
        self.autoplay_btn.config_style(text="Auto-play", bg=PALETTE["autoplay"], hover_bg=PALETTE["autoplay_hover"])
        self.scan_btn.config_style(state="normal")
        self.autoplay_status_var.set(reason)

    def _analyze_manual(self):
        text = self.manual_entry.get()
        tokens = [t for t in re.split(r"[,\n]+", text) if t.strip()]
        results = []
        for tok in tokens:
            entry, score = self.db.lookup(tok)
            results.append((entry, tok.strip(), score))
        self.last_results = results
        self._rerender_results()

    def _rerender_results(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        char = self.selected_character.get()

        scored = []
        unknown = []
        for entry, raw, match_score in self.last_results:
            if entry is None:
                unknown.append(raw)
                continue
            pts = self.db.points_for(entry, char)
            scored.append((pts, entry, raw))

        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored and not unknown:
            self.best_var.set("Scan the screen (or type words below) to see results.")
            return

        best_score = scored[0][0] if scored else None

        row_num = 0
        for pts, entry, raw in scored:
            display = entry["display"]
            if self.current_act == "2" and self.db.is_act2_inherited(entry, char):
                display = f"{display} 🎀"
            row_vals = (
                display,
                self.db.points_for(entry, "sayori"),
                self.db.points_for(entry, "natsuki"),
                self.db.points_for(entry, "yuri"),
                self.db.points_for(entry, "monika"),
            )
            tags = []
            if pts == best_score:
                tags.append("best")
            tags.append("even" if row_num % 2 == 0 else "odd")
            self.tree.insert("", "end", values=row_vals, tags=tuple(tags))
            row_num += 1

        for raw in unknown:
            tags = ["unknown"]
            tags.append("even" if row_num % 2 == 0 else "odd")
            self.tree.insert(
                "", "end", values=(f"{raw} (unknown)", "-", "-", "-", "-"),
                tags=tuple(tags),
            )
            row_num += 1

        if char == "monika":
            self.best_var.set(
                "Monika likes every word equally - pick any word!"
            )
        elif scored:
            best_words = []
            for pts, e, raw in scored:
                if pts != best_score:
                    continue
                label = e["display"]
                if self.current_act == "2" and self.db.is_act2_inherited(e, char):
                    label += " 🎀"
                best_words.append(label)
            char_color = CHARACTER_COLORS[char]
            inherited_note = (
                "  (🎀 = originally Sayori's, now usable here in Act 2)"
                if self.current_act == "2" and any("🎀" in w for w in best_words)
                else ""
            )
            self.best_var.set(
                f"✨ Best for {CHARACTER_LABELS[char]}: "
                f"{', '.join(best_words)} ({best_score} points){inherited_note}"
            )
        else:
            self.best_var.set("No recognized words yet - try scanning again.")


def find_tesseract_executable():
    """find tesseract"""
    configured_path = load_app_settings().get("tesseract_path")
    candidates = [
        configured_path,
        shutil.which("tesseract.exe"),
        shutil.which("tesseract"),
        os.path.join(RESOURCE_DIR, "tesseract", "tesseract.exe"),
    ]
    frozen_dir = getattr(sys, "_MEIPASS", None)
    if frozen_dir:
        candidates.insert(0, os.path.join(frozen_dir, "tesseract", "tesseract.exe"))
    if sys.platform == "win32":
        for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def configure_tesseract():
    """tell pytesseract where tesseract is"""
    executable = find_tesseract_executable()
    if executable and pytesseract is not None:
        pytesseract.pytesseract.tesseract_cmd = executable
    return executable


def missing_dependencies():
    """return anything that's missing"""
    missing = []
    if mss is None:
        missing.append("mss")
    if Image is None or ImageTk is None:
        missing.append("Pillow")
    if pytesseract is None:
        missing.append("pytesseract")
    if process is None:
        missing.append("rapidfuzz")
    if pyautogui is None:
        missing.append("pyautogui")
    if keyboard is None:
        missing.append("keyboard (optional F12 stop hotkey)")
    if find_tesseract_executable() is None:
        missing.append("Tesseract OCR")
    return missing


def check_dependencies():
    missing = missing_dependencies()
    if missing:
        print("NOTE: some dependencies are missing:")
        for item in missing:
            print(f"  - {item}")
        print(
            "The manual 'type the words in' box may still work.\n"
            "Run install_windows.bat (Windows) or install the requirements "
            "manually.\n"
        )


def run_windows_installer():
    """run the windows installer and restart"""
    if sys.platform != "win32":
        messagebox.showerror(
            "Windows setup only",
            "install_windows.bat can only run on Windows.",
        )
        return False
    if not os.path.isfile(WINDOWS_INSTALLER_PATH):
        messagebox.showerror(
            "Installer not found",
            "install_windows.bat is missing from the application folder.\n\n"
            "Install the missing components manually, then restart the app.",
        )
        return False

    try:
        installer_command = [
            "cmd.exe", "/d", "/c", "call", WINDOWS_INSTALLER_PATH
        ]
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        result = subprocess.run(
            installer_command,
            cwd=APP_DIR,
            creationflags=creationflags,
            check=False,
        )
    except Exception as exc:
        messagebox.showerror("Automatic setup failed", str(exc))
        return False

    if result.returncode != 0:
        messagebox.showerror(
            "Automatic setup failed",
            "The installer did not finish successfully.\n\n"
            "You can run install_windows.bat manually for more details.",
        )
        return False

    messagebox.showinfo(
        "Setup complete",
        "Everything needed was installed. The assistant will now restart "
        "using the new environment.",
    )
    try:
        if getattr(sys, "frozen", False):
            restart_command = [sys.executable]
        else:
            venv_python = os.path.join(APP_DIR, ".venv", "Scripts", "pythonw.exe")
            launcher = venv_python if os.path.isfile(venv_python) else sys.executable
            restart_command = [launcher, os.path.abspath(__file__), *sys.argv[1:]]
        subprocess.Popen(
            restart_command,
            cwd=APP_DIR,
        )
        return True
    except Exception as exc:
        messagebox.showerror(
            "Restart failed",
            f"Setup finished, but the assistant could not restart:\n\n{exc}",
        )
        return False


def prompt_to_install_dependencies():
    """offer the windows installer when setup is missing"""
    if sys.platform != "win32":
        return True

    configure_tesseract()
    missing = missing_dependencies()
    if not missing:
        return True

    missing_text = "\n".join(f"  • {item}" for item in missing)
    message = (
        "The following components are missing:\n\n"
        f"{missing_text}\n\n"
        "Would you like to download and install everything automatically?\n\n"
        "Warning: installing Tesseract may require administrator/UAC "
        "permission. It may also add Tesseract to your PATH so OCR can "
        "work correctly."
    )
    if not messagebox.askyesno("Missing setup components", message):
        return True
    return not run_windows_installer()


if __name__ == "__main__":
    if prompt_to_install_dependencies():
        configure_tesseract()
        check_dependencies()
        app = App()
        app.mainloop()
