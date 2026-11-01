"""Render simple e-paper screens without requiring display hardware."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


DEFAULT_FONT = Path(__file__).resolve().parent / "e-Paper/RaspberryPi_JetsonNano/python/pic/Font.ttc"


def render_text(width, height, text="Hello, e-paper!", font_path=DEFAULT_FONT, font_size=24):
    if width <= 0 or height <= 0 or font_size <= 0:
        raise ValueError("Image dimensions and font size must be positive")
    font = ImageFont.truetype(str(font_path), font_size)
    image = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(image)
    text = " ".join(text.splitlines())
    available_width = max(0, width - 20)
    if draw.textlength(text, font=font) > available_width:
        while text and draw.textlength(text + "...", font=font) > available_width:
            text = text[:-1]
        text = text + "..." if text else ""
    draw.text((10, 10), text, font=font, fill=0)
    return image