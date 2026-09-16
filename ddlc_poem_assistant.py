"""
DDLC Plus - Poem Minigame Word Assistant
==========================================
Reads the word tiles on your screen during a DDLC Plus poem minigame,
looks them up in a researched word/point database, and tells you which
on-screen word scores the most points for whichever character you pick.

No AI model / API calls are used at runtime - this is just:
  screenshot -> OCR -> dictionary lookup -> sort by points
which is both faster and more reliable than asking an LLM to "guess"
DDLC's scoring, since the scoring is a fixed, known table.

--------------------------------------------------------------------
SETUP (see README.md for full details)
--------------------------------------------------------------------
1. Install Python 3.9+
2. Install Tesseract OCR (separate program, not a pip package):
     Windows: https://github.com/UB-Mannheim/tesseract/wiki
     macOS:   brew install tesseract
     Linux:   sudo apt install tesseract-ocr
3. pip install -r requirements.txt
4. python ddlc_poem_assistant.py
--------------------------------------------------------------------
"""

import ctypes
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox


def _set_windows_dpi_awareness():
    """Tell Windows to render this Tk window at the display's native DPI."""
    if sys.platform != "win32":
        return
    try:
        ctypes.OleDLL("shcore").SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        # Older Windows versions may not expose shcore; this fallback is the
        # older system-wide DPI-aware API and is still better than bitmap
        # scaling the entire Tk window.
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


_set_windows_dpi_awareness()

# ---- Optional/third-party deps -------------------------------------------
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
    pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort instantly
    pyautogui.PAUSE = 0        # we manage our own delays explicitly
except ImportError:
    pyautogui = None

try:
    import keyboard  # optional: lets the Stop hotkey work even if our window isn't focused
except ImportError:
    keyboard = None

# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORD_DATA_PATH = os.path.join(BASE_DIR, "word_data.json")
REGION_CONFIG_PATH = os.path.join(BASE_DIR, "capture_region.json")

CHARACTERS = ["sayori", "natsuki", "yuri", "monika"]

CHARACTER_COLORS = {
    "sayori": "#ff9dc0",   # ribbon pink
    "natsuki": "#ff6e78",  # cupcake coral-pink
    "yuri": "#9b6fc9",     # purple
    "monika": "#b8cf5a",   # yellow-green (chalkboard/"just Monika")
}


def lighten_color(hex_color, factor=0.6):
    """Blend a hex color toward white by `factor` (0=no change, 1=white)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def darken_color(hex_color, factor=0.15):
    """Darken a hex color by `factor` (0=no change, 1=black) - used for
    a subtle hover shade on already-solid-colored buttons."""
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

# ---- DDLC-inspired color palette (soft sakura pink/cream) ------------------
PALETTE = {
    "bg": "#fdf1f6",          # soft pink-cream page background
    "card": "#fffbfd",        # near-white, pink-tinted card background
    "card_border": "#f6d9e6",
    "header_from": "#ffb3d1", # header gradient, pink -> soft blush
    "header_to": "#ffe3ee",
    "text": "#4a2e3d",        # deep plum - readable on pink, not harsh black
    "text_muted": "#a18290",
    "accent": "#ff6f9c",      # sakura pink - primary action (Scan Screen)
    "accent_hover": "#ff4d84",
    "accent_soft": "#ffe1ec",
    "setup": "#b48ad1",       # soft lavender - secondary action (Setup)
    "setup_hover": "#9d6fbd",
    "autoplay": "#7cc49a",    # soft mint green - auto-play idle ("go")
    "autoplay_hover": "#5fae80",
    "stop": "#e0607e",        # warm rose-red - auto-play active/stop
    "stop_hover": "#c94a68",
    "best_highlight": "#ffedb0",
    "row_alt": "#fff5f9",
    "unknown_text": "#b096a0",
}

FONT = "Aller"  # DDLC's actual UI font

# Fallback chain if Aller isn't installed on this machine - tried in order.
FONT_FALLBACKS = ["Aller", "Segoe UI", "SF Pro Text", "Helvetica Neue",
                   "Ubuntu", "Noto Sans", "DejaVu Sans", "Arial"]

ALLER_AVAILABLE = False  # set by resolve_font() once we have a Tk root to check against


def resolve_font(root):
    """Pick the best available font, preferring DDLC's real UI font (Aller)
    and falling back through a few clean sans-serif options if it isn't
    installed. Mutates the module-level FONT used everywhere in the UI."""
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


FUZZY_MATCH_THRESHOLD = 78  # 0-100, lower = more forgiving of OCR typos

# ---- Auto-play settings ----------------------------------------------------
OCR_SCALE_FACTOR = 2          # must match preprocess_image()'s resize multiplier
AUTOPLAY_CLICK_DELAY = 1.3    # seconds to wait after each click before re-scanning
AUTOPLAY_MAX_ROUNDS = 20      # hard safety cap - a full poem is ~20 word picks
AUTOPLAY_END_STREAK = 2       # consecutive "looks ended" scans required before stopping
AUTOPLAY_MIN_KNOWN_WORDS = 2  # fewer recognized words than this -> tiles look gone
AUTOPLAY_STOP_HOTKEY = "f12"  # global stop hotkey (only active if 'keyboard' is installed)

UI_SUPERSAMPLE = 4  # render PIL shapes at 4x, then downsample for smooth edges


# ---------------------------------------------------------------------------
class WordDatabase:
    """Loads the word -> points table and handles fuzzy lookups."""

    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.meta = data.get("meta", {})
        # normalized_key -> {"display": str, "points": {char: int}, "key": str}
        self.entries = {}
        for word, points in data["words"].items():
            key = self._normalize(word)
            self.entries[key] = {
                "display": word.capitalize() if "-" not in word else word.title(),
                "points": points,
                "key": key,
            }
        self._keys = list(self.entries.keys())
        self._word_set = set(self._keys)  # For fast lookup in split detection

        # Act 2: Sayori is gone, and her Act-1 words become available tile
        # options for Natsuki and/or Yuri. Their POINT VALUES do not change
        # (verified against the game's actual mechanic - it's simply that
        # Sayori's column stops counting), but we track which words these
        # are so the UI can label them and so Sayori can be disabled as an
        # optimization target once Act 2 starts.
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
        """Return (entry_dict, match_score) for a raw OCR token, or (None, 0)."""
        key = self._normalize(raw_token)
        if not key:
            return None, 0
        if key in self.entries:
            return self.entries[key], 100
        if process is None:
            return None, 0  # rapidfuzz not installed - exact match only
        match = process.extractOne(key, self._keys, scorer=fuzz.ratio)
        if match and match[1] >= FUZZY_MATCH_THRESHOLD:
            matched_key = match[0]
            return self.entries[matched_key], match[1]
        return None, 0

    def points_for(self, entry, character):
        if character == "monika":
            return 3  # Monika likes everything equally
        return entry["points"].get(character, 0)

    def is_act2_inherited(self, entry, character):
        """True if this word was originally Sayori's and only became a
        valid Act 2 tile option for `character` after she was gone."""
        return entry["key"] in self.act2_transfers.get(character, set())


# ---------------------------------------------------------------------------
def preprocess_image(pil_img):
    """Light preprocessing to help OCR accuracy on game-UI text."""
    img = pil_img.convert("L")  # grayscale
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    return img


def split_merged_tokens(token, word_set):
    """
    Attempt to split a merged token like 'unendingawesome' or 'sing fester' into known words.
    Returns list of candidate words found, or [token] if no split is found.
    """
    # First, try splitting on spaces if present
    if ' ' in token:
        space_split = token.lower().split()
        all_known = True
        for word in space_split:
            # Normalize the word the same way the database does
            normalized = word.replace("-", "").replace("'", "")
            normalized = re.sub(r"[^a-z0-9]", "", normalized)
            if normalized not in word_set:
                all_known = False
                break
        if all_known and len(space_split) > 1:
            return space_split
    
    # If no spaces or space-split didn't work, try finding word boundaries
    token_lower = token.lower().replace(" ", "")  # Remove spaces for boundary detection
    results = []
    
    def find_words(s, current_split):
        if not s:
            results.append(current_split[:])
            return
        # Try matching words of different lengths
        for i in range(min(len(s), 20), 0, -1):
            candidate = s[:i]
            if candidate in word_set:
                current_split.append(candidate)
                find_words(s[i:], current_split)
                current_split.pop()
    
    find_words(token_lower, [])
    
    # Return the split with most words (most likely correct)
    if results:
        best_split = max(results, key=len)
        if len(best_split) > 1:  # Only return if we actually split something
            return best_split
    
    return [token]


def extract_words_from_image(pil_img):
    """Run OCR on an image and return a list of candidate word tokens."""
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract is not installed, or the Tesseract program isn't on "
            "your PATH. See README.md for setup instructions."
        )
    processed = preprocess_image(pil_img)
    raw_text = pytesseract.image_to_string(processed, config="--psm 6")
    # Split on whitespace/newlines; keep internal hyphens (e.g. "Doki-Doki")
    tokens = re.split(r"[\n\r]+|\s{2,}|\s(?=[A-Z])", raw_text)
    tokens = [t.strip(" .,:;!?\"'()[]") for t in tokens]
    tokens = [t for t in tokens if len(t) >= 2 and any(c.isalpha() for c in t)]
    return tokens


def extract_words_with_boxes(pil_img):
    """Like extract_words_from_image, but also returns each token's pixel
    bounding box (scaled back to the ORIGINAL, un-upscaled region-image
    coordinates) so auto-play knows where on screen to click.

    Uses Tesseract's own per-word segmentation (image_to_data with sparse-
    text mode) rather than the line-reconstruction approach, since we need
    discrete boxes, not just a flat list of tokens. This is only used by
    auto-play - the manual "Scan Screen" flow is untouched.
    """
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
                # divide out the 2x upscale from preprocess_image() so these
                # line up with the original captured-region image
                "left": data["left"][i] / OCR_SCALE_FACTOR,
                "top": data["top"][i] / OCR_SCALE_FACTOR,
                "width": data["width"][i] / OCR_SCALE_FACTOR,
                "height": data["height"][i] / OCR_SCALE_FACTOR,
            })
    return boxes


def grab_region(region):
    """region = (left, top, width, height) in absolute screen coords."""
    if mss is None:
        raise RuntimeError("The 'mss' package is not installed. See README.md.")
    left, top, width, height = region
    with mss.mss() as sct:
        monitor = {"left": left, "top": top, "width": width, "height": height}
        shot = sct.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


# ---------------------------------------------------------------------------
def draw_gradient(canvas, width, height, color1, color2):
    """Paint a left-to-right gradient onto a Canvas (used for the header)."""
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
    """Draw a row of half-circle scallops along y (DDLC's title-screen
    border look) by punching pale-background circles out of whatever's
    drawn above them."""
    step = scallop_r * 2
    x = -scallop_r
    while x < width + scallop_r:
        canvas.create_oval(
            x - scallop_r, y - scallop_r, x + scallop_r, y + scallop_r,
            fill=color, outline=color,
        )
        x += step


def draw_polka_dots(canvas, width, height, dot_color, radius=3, spacing=24, y_start=0):
    """Scatter small polka dots across a canvas region - DDLC's title-screen
    background pattern, drawn from scratch (not a copied asset)."""
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
    """Return the Pillow resampling enum across supported Pillow versions."""
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def _make_rounded_image(width, height, fill, outline=None, radius=0, outline_width=1):
    """Create an anti-aliased transparent rounded shape, or None without PIL."""
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
    """Render header/footer decoration with supersampled PIL primitives."""
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
    """A pill/rounded-rectangle button (DDLC's actual button shape) built
    on a Canvas, since plain tk.Button can't do rounded corners. Exposes a
    tk.Button-ish surface (.config_style(), .state) so the rest of the app
    can treat it like a normal button."""

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
        # Use the ACTUAL current size (winfo_width/height), not the
        # originally-requested -width/-height: pack(fill="x") stretches the
        # canvas beyond what it was constructed with, and self["width"]
        # only ever reflects the construction-time request, not that.
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
            # Keep the original Canvas implementation as a dependency-safe
            # fallback if Pillow/ImageTk cannot render the smooth shape.
            self.create_polygon(pts, smooth=True, fill=color, outline=color)
            if self.selected:
                # A slightly darker ring around a selected button - a clear,
                # persistent "this one is picked" indicator independent of
                # mouse position (unlike hover, which comes and goes).
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
        """tk.Button-ish .config() replacement - only touches what's passed.
        NOTE: this widget is a Canvas, not a real Button - plain .config()
        does NOT support bg/fg/text/state/relief the way a tk.Button does.
        Always go through this method to change a RoundedButton's look."""
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
    """A container with a rounded-rectangle border (DDLC's soft dialogue-box
    look), built the same way as RoundedButton: a background Canvas with the
    real content Frame (`.body`) embedded via create_window. Pack/grid
    children into `.body` exactly like you would into a plain tk.Frame - the
    card grows to fit its content automatically, and also stretches to fill
    extra space if the RoundedCard itself is packed with fill/expand."""

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
        # The card's minimum size comes from its content's natural size -
        # this is what makes fill="x"-only (non-expanding) cards hug their
        # content instead of collapsing to 1x1.
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
            # Let the body fill whatever space the canvas actually has (this
            # is what lets a fill="both", expand=True card - like the results
            # table - actually grow, instead of staying at its minimum size).
            self.canvas.coords(self._win, 4, 4)
            self.canvas.itemconfig(self._win, width=max(w - 8, 1), height=max(h - 8, 1))


# ---------------------------------------------------------------------------
class RegionSelector(tk.Toplevel):
    """Full virtual-desktop transparent overlay for click-drag region selection."""

    def __init__(self, master, on_selected):
        super().__init__(master)
        self.on_selected = on_selected

        if mss is not None:
            with mss.mss() as sct:
                mon = sct.monitors[0]  # combined virtual screen
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


# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DDLC+ Poem Word Assistant")
        self.geometry("520x720")
        self.minsize(480, 620)
        self.attributes("-topmost", True)
        self.configure(bg=PALETTE["bg"])

        try:
            self.db = WordDatabase(WORD_DATA_PATH)
        except Exception as e:
            messagebox.showerror("Data error", f"Couldn't load word_data.json:\n{e}")
            self.destroy()
            return

        self.region = self._load_region()
        self.selected_character = tk.StringVar(value="sayori")
        self.current_act = "1"  # "1" or "2" - plain attribute, read from the
                                 # auto-play thread too, same pattern as click_delay
        self.last_results = []  # list of (entry, ocr_raw, match_score)

        self.autoplay_active = False
        self.autoplay_stop_requested = False
        # Plain attribute (not a Tk variable) so the auto-play background
        # thread can read it directly - matches how autoplay_stop_requested
        # is already shared across threads elsewhere in this file.
        self.click_delay = AUTOPLAY_CLICK_DELAY

        # Configure modern ttk style
        self.style = ttk.Style()
        self.style.theme_use("clam")

        # Treeview styling
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

        resolve_font(self)  # prefer Aller (DDLC's real font) if installed
        self._set_window_icon()
        self._build_ui()

    # -- persistence ---------------------------------------------------
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
        """Draw a simple pink heart and use it as the window/taskbar icon -
        no external asset files needed."""
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
            pass  # purely decorative - never worth failing startup over

    # -- UI --------------------------------------------------------------
    def _build_ui(self):
        # Header - soft pink gradient banner with polka dots + a scalloped
        # bottom edge (DDLC's actual title-screen look)
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
                # Keep the PhotoImage alive; Tk otherwise garbage-collects it
                # after this callback and the background can turn blank.
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

        # Footer - a thin polka-dot strip, mirroring the header for symmetry.
        # Packed BEFORE the expanding content frame below: pack() allocates
        # space in call order, so a side="bottom" widget packed after an
        # expand=True sibling gets squeezed to ~0px - it has to claim its
        # slice first.
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

        # Main content area
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

        # Act selector card
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

        # Character selection card
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

        # Action buttons card
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

        # Auto-play delay control
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

        # Status
        self.status_var = tk.StringVar(value=self._region_status_text())
        status_label = tk.Label(
            content, textvariable=self.status_var, fg=PALETTE["text_muted"],
            bg=PALETTE["bg"], font=(FONT, 9), anchor="w"
        )
        status_label.pack(fill="x", pady=(0, 2))

        # Auto-play status (blank until auto-play is used)
        self.autoplay_status_var = tk.StringVar(value="")
        autoplay_status_label = tk.Label(
            content, textvariable=self.autoplay_status_var, fg=PALETTE["stop_hover"],
            bg=PALETTE["bg"], font=(FONT, 9, "bold"), anchor="w"
        )
        autoplay_status_label.pack(fill="x", pady=(0, 8))

        # Best recommendation card - styled like DDLC's dialogue box, with a
        # small colored nameplate tag for whichever character is selected
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

        # Results table card
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

        # Row styling
        self.tree.tag_configure("best", background=PALETTE["best_highlight"], font=(FONT, 10, "bold"))
        self.tree.tag_configure("unknown", foreground=PALETTE["unknown_text"], font=(FONT, 10, "italic"))
        self.tree.tag_configure("even", background=PALETTE["row_alt"])
        self.tree.tag_configure("odd", background=PALETTE["card"])

        self.tree.pack(fill="both", expand=True, side="left")

        scroll = ttk.Scrollbar(table_container, command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        # Manual entry card
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

    def _select_character(self, char):
        self.selected_character.set(char)
        self._update_character_buttons()
        self._rerender_results()

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

    def _region_status_text(self):
        if self.region:
            return f"Capture region set: {self.region}"
        return "No capture region set yet - click 'Setup' first."

    # -- actions -----------------------------------------------------------
    def _select_region(self):
        self.withdraw()
        self.after(150, self._open_region_selector)

    def _open_region_selector(self):
        def done(region):
            self.region = region
            self._save_region(region)
            self.status_var.set(self._region_status_text())
            self.deiconify()

        selector = RegionSelector(self, done)
        selector.grab_set()
        # Ensure the main window comes back even if the user pressed Esc
        self.wait_window(selector)
        self.deiconify()

    def _scan_clicked(self):
        if not self.region:
            messagebox.showinfo(
                "No region set", "Click 'Select Region' first and drag a box "
                "around the row/grid of poem words in the game."
            )
            return
        self.scan_btn.config_style(state="disabled", text="Scanning...")
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        try:
            img = grab_region(self.region)
            tokens = extract_words_from_image(img)
            results = []
            seen = set()
            
            for tok in tokens:
                # First try exact/fuzzy lookup
                entry, score = self.db.lookup(tok)
                
                if entry is None:
                    # Token not found - might be merged words
                    split_words = split_merged_tokens(tok, self.db._word_set)
                    if len(split_words) > 1:
                        # Successfully split into multiple known words
                        for word in split_words:
                            entry, score = self.db.lookup(word)
                            if entry:
                                key = entry["display"]
                                if key not in seen:
                                    seen.add(key)
                                    results.append((entry, word, score))
                    else:
                        # Still unknown after split attempt
                        key = tok.lower()
                        if key not in seen:
                            seen.add(key)
                            results.append((None, tok, 0))
                else:
                    # Found it directly
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

    # -- auto-play -----------------------------------------------------------
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
                pass  # hotkey registration can fail on some setups; corner fail-safe still works

        self._autoplay_countdown(3)

    def _autoplay_countdown(self, seconds_left):
        if self.autoplay_stop_requested:
            self._autoplay_finished("Stopped before starting.")
            return
        if seconds_left <= 0:
            self.autoplay_status_var.set("Auto-play running...")
            # Capture the target character on the main thread - the loop
            # thread must not call self.selected_character.get() itself
            # (Tk variables aren't safe to read from a background thread).
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
        # UI reset happens in _autoplay_loop's finally block via self.after(...)

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
            # Combine tags: best + stripe
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


# ---------------------------------------------------------------------------
def check_dependencies():
    missing = []
    if mss is None:
        missing.append("mss")
    if Image is None:
        missing.append("Pillow")
    if pytesseract is None:
        missing.append("pytesseract (+ the Tesseract OCR program itself)")
    if process is None:
        missing.append("rapidfuzz")
    if missing:
        print("NOTE: some optional dependencies are missing:")
        for m in missing:
            print(f"  - {m}")
        print(
            "The manual 'type the words in' box will still work.\n"
            "Run: pip install -r requirements.txt   (and install Tesseract "
            "separately - see README.md)\n"
        )


if __name__ == "__main__":
    check_dependencies()
    app = App()
    app.mainloop()
