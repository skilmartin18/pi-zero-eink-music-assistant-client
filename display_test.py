"""Preview a text screen or show it once on a Waveshare display."""

import argparse
import ast
import importlib
from pathlib import Path
import sys

from display_render import DEFAULT_FONT, render_text


WAVESHARE_LIB = Path(__file__).resolve().parent / "e-Paper/RaspberryPi_JetsonNano/python/lib"
RESERVED_PINS = {0, 1, 8, 10, 11, 17, 18, 19, 21, 24, 25}


def configured_power_pin():
    tree = ast.parse((WAVESHARE_LIB / "waveshare_epd/epdconfig.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "RaspberryPi":
            for statement in node.body:
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "PWR_PIN"
                    for target in statement.targets
                ):
                    return ast.literal_eval(statement.value)
    raise ValueError("Cannot determine the Waveshare Raspberry Pi power pin")


def load_driver(model, power_pin):
    if model != "epd2in9_V2":
        raise ValueError("Unsupported display model")
    if power_pin not in range(2, 28) or power_pin in RESERVED_PINS:
        raise ValueError("Power pin conflicts with the DAC or display signals")
    if configured_power_pin() != power_pin:
        raise ValueError("Power pin does not match the bundled Waveshare RaspberryPi configuration")
    sys.path.insert(0, str(WAVESHARE_LIB))
    return importlib.import_module("waveshare_epd." + model)


def show_on_display(driver, text, font_path, font_size):
    display = driver.EPD()
    initialized = False
    try:
        image = render_text(display.height, display.width, text, font_path, font_size)
        if display.init() != 0:
            raise RuntimeError("Display initialization failed")
        initialized = True
        display.display(display.getbuffer(image))
    finally:
        if initialized:
            try:
                display.sleep()
            except BaseException:
                driver.epdconfig.module_exit(cleanup=True)
                raise
        else:
            driver.epdconfig.module_exit(cleanup=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", metavar="PNG", help="save a preview without using GPIO")
    mode.add_argument("--display", choices=["epd2in9_V2"], help="exact display driver")
    parser.add_argument("--power-pin", type=int, help="verified display power-control BCM pin")
    parser.add_argument("--text", default="Hello, e-paper!")
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument("--font-size", type=int, default=24)
    parser.add_argument("--width", type=int, default=296, help="preview width in pixels")
    parser.add_argument("--height", type=int, default=128, help="preview height in pixels")
    args = parser.parse_args()
    if args.preview:
        render_text(args.width, args.height, args.text, args.font, args.font_size).save(args.preview)
        print("Saved preview to {}".format(args.preview))
        return
    if args.power_pin is None:
        parser.error("--display requires a verified --power-pin")
    try:
        driver = load_driver(args.display, args.power_pin)
        show_on_display(driver, args.text, args.font, args.font_size)
    except (ValueError, ImportError, OSError, RuntimeError) as error:
        parser.exit(1, "Display test failed: {}\n".format(error))


if __name__ == "__main__":
    main()