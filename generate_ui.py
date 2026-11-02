"""Render Music Assistant now-playing data as a monochrome Pillow image."""

import math
from functools import lru_cache
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageOps
from qrcode.exceptions import DataOverflowError

FONT_DIR = Path(__file__).resolve().parent / "fonts" / "static"
DEFAULT_FONT = FONT_DIR / "PixelifySans-Regular.ttf"
DEFAULT_BOLD_FONT = FONT_DIR / "PixelifySans-Bold.ttf"


def _fit_text(draw, text, font, max_width):
    text = " ".join(str(text).split())
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=font) > max_width:
        text = text[:-1]
    return text.rstrip() + "..." if text else ""


def _format_time(seconds):
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        return "--:--"
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return "{}:{:02d}".format(minutes, seconds)


def _playback_label(state):
    state = getattr(state, "value", state)
    return {"playing": "Playing", "paused": "Paused", "idle": "Paused"}.get(state, "Unknown")


def _draw_wifi(draw, x, signal):
    if not isinstance(signal, (int, float)) or not math.isfinite(signal):
        draw.arc((x, 3, x + 14, 17), 220, 320, fill=0)
        draw.line((x + 2, 3, x + 13, 14), fill=0, width=2)
        return

    draw.arc((x + 4, 9, x + 10, 15), 220, 320, fill=0, width=1)
    if signal >= -80:
        draw.arc((x + 2, 6, x + 12, 16), 220, 320, fill=0, width=1)
    if signal >= -60:
        draw.arc((x, 3, x + 14, 17), 220, 320, fill=0, width=1)


@lru_cache(maxsize=1)
def _party_qr(url, max_size=72):
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=1, border=4)
    try:
        code.add_data(url)
        code.make(fit=True)
    except (DataOverflowError, ValueError):
        return None
    modules = code.modules_count + 2 * code.border
    scale = max_size // modules
    if not scale:
        return None
    qr_image = code.make_image(fill_color="black", back_color="white").get_image().convert("1")
    return qr_image.resize((modules * scale, modules * scale), Image.Resampling.NEAREST)


def render_connection_error(player_name, server_url, error_message, width=296, height=128, font_path=DEFAULT_FONT):
    image = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(image)
    message = "{}: {} ({}).".format(player_name, error_message, server_url)
    for size in range(16, 7, -1):
        font = ImageFont.truetype(str(font_path), size)
        lines = []
        remaining = message
        while remaining:
            low, high = 0, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if draw.textlength(remaining[:middle], font=font) <= width - 16:
                    low = middle
                else:
                    high = middle - 1
            length = low
            if length == 0:
                break
            if length < len(remaining):
                space = remaining.rfind(" ", 0, length + 1)
                if space > 0:
                    length = space
            lines.append(remaining[:length].strip())
            remaining = remaining[length:].lstrip()
        if not remaining and len(lines) * (size + 3) <= height - 16:
            break
    line_height = size + 3
    max_lines = max(1, (height - 16) // line_height)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _fit_text(draw, lines[-1] + "...", font, width - 16)
    y = max(0, (height - len(lines) * line_height) // 2)
    for line in lines:
        draw.text((8, y), line, font=font, fill=0)
        y += line_height
    return image


def render_now_playing(data, width=296, height=128, font_path=DEFAULT_FONT, bold_font_path=None):
    """Return a landscape 1-bit image from a dictionary of player data."""
    if width < 240 or height < 128:
        raise ValueError("Now-playing layout requires at least 240 x 128 pixels")

    image = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(image)
    if bold_font_path is None:
        bold_font_path = DEFAULT_BOLD_FONT if Path(font_path) == DEFAULT_FONT else font_path
    title_font = ImageFont.truetype(str(bold_font_path), 19)
    body_font = ImageFont.truetype(str(font_path), 14)
    small_font = ImageFont.truetype(str(font_path), 11)

    state = _playback_label(data.get("state"))
    volume = data.get("volume")
    volume_text = "Vol: {}%".format(volume) if volume is not None else "Vol: --"
    draw.text((8, 1), _fit_text(draw, state, small_font, width - 176), font=small_font, fill=0)
    draw.text((width - 160, 1), _fit_text(draw, data.get("player_name") or "Unknown player", small_font, 65), font=small_font, fill=0)
    draw.text((width - 86, 1), _fit_text(draw, volume_text, small_font, 60), font=small_font, fill=0)
    _draw_wifi(draw, width - 24, data.get("wifi_signal_dbm"))
    draw.line((8, 16, width - 9, 16), fill=0)

    artwork = data.get("artwork")
    if artwork:
        try:
            with Image.open(artwork) as cover:
                cover = ImageOps.fit(ImageOps.exif_transpose(cover).convert("RGB"), (72, 72))
                image.paste(cover.convert("L").convert("1"), (8, 21))
        except (OSError, ValueError):
            pass
    draw.rectangle((8, 21, 79, 92), outline=0)

    text_x = 88
    text_right = width - 8
    progress_right = width - 9
    party_url = data.get("party_url")
    qr_image = _party_qr(party_url) if party_url else None
    reserved_regions = []
    if qr_image:
        qr_x = width - 8 - qr_image.width
        qr_y = 97 - qr_image.height
        image.paste(qr_image, (qr_x, qr_y))
        join_position = (qr_x + qr_image.width // 2, qr_y - 1)
        for size in range(14, 7, -1):
            join_font = ImageFont.truetype(str(font_path), size)
            join_bounds = draw.textbbox(join_position, "join", font=join_font, anchor="mb")
            if join_bounds[1] >= 18:
                break
        draw.text(join_position, "join", font=join_font, fill=0, anchor="mb")
        reserved_regions = [(qr_x, qr_y, qr_x + qr_image.width, 97), join_bounds]
    for y, value, font in (
        (20, data.get("title") or "Unknown song", title_font),
        (45, data.get("artist") or "Unknown artist", body_font),
        (64, data.get("album") or "Unknown album", body_font),
        (82, "Next: " + str(data.get("next_title") or "--"), small_font),
    ):
        bounds = draw.textbbox((text_x, y), str(value), font=font)
        line_right = text_right
        for left, top, right, bottom in reserved_regions:
            if bounds[1] < bottom and bounds[3] > top:
                line_right = min(line_right, left - 4)
        draw.text((text_x, y), _fit_text(draw, value, font, line_right - text_x), font=font, fill=0)

    elapsed = data.get("elapsed")
    duration = data.get("duration")
    draw.rectangle((8, 99, progress_right, 104), outline=0)
    if (isinstance(elapsed, (int, float)) and math.isfinite(elapsed)
            and isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0):
        progress = max(0, min(1, elapsed / duration))
        fill_width = int((progress_right - 9) * progress)
        if fill_width:
            draw.rectangle((9, 100, 8 + fill_width, 103), fill=0)
    draw.text((8, 108), _format_time(elapsed), font=small_font, fill=0)
    duration_text = _format_time(duration)
    draw.text((progress_right + 1 - draw.textlength(duration_text, font=small_font), 108),
              duration_text, font=small_font, fill=0)
    return image