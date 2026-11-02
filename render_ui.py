"""Output now-playing images to a PNG or a Waveshare display."""

from pathlib import Path
from time import monotonic

# for testing we want to save an image as png, not display to the screen
class PreviewOutput:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        print("Output mode: preview ({})".format(self.path), flush=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def update(self, image, song_key=None, urgent=False):
        image.save(self.path)


class DisplayOutput:
    def __init__(self, driver, timing=False):
        self.driver = driver
        self.timing = timing
        self.display = driver.EPD()
        self.has_displayed = False
        self.previous_song = None
        self.previous_image = None
        self.initialized = False

    def __enter__(self):
        try:
            print("Output mode: e-paper; initializing display...", flush=True)
            if self.display.init() != 0:
                raise RuntimeError("Display initialization failed")
            self.initialized = True
            print("Display initialized; waiting for Music Assistant data...", flush=True)
        except BaseException:
            self.driver.epdconfig.module_exit(cleanup=True)
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.initialized:
            try:
                self.display.sleep()
            except BaseException:
                self.driver.epdconfig.module_exit(cleanup=True)
                raise
        else:
            self.driver.epdconfig.module_exit(cleanup=True)
        return False

    def update(self, image, song_key=None, urgent=False):
        if image.mode != "1" or image.size != (self.display.height, self.display.width):
            raise ValueError("Display image must be a landscape 1-bit image matching the panel")
        
        if not self.has_displayed or (not urgent and song_key != self.previous_song):
            
            # reinitialise the display to clear stuff (probably a better way of doing this outside of reinitialising)
            if self.has_displayed:
                self.display.sleep()
                self.initialized = False
                if self.display.init() != 0:
                    raise RuntimeError("Display reinitialization failed")
                self.initialized = True
                
            self._display_image(image, full=True)
            self.previous_song = None if urgent else song_key
            self.previous_image = image.copy()
            
        else:
            if image.tobytes() == self.previous_image.tobytes():
                return False
            self._display_image(image, full=False)
            self.previous_image = image.copy()
        self.has_displayed = True
        return True

    def _display_image(self, image, full):
        started = monotonic()
        buffer = self.display.getbuffer(image)
        converted = monotonic()
        if full:
            self.display.display_Base(buffer)
        else:
            self.display.display_Partial(buffer)
        finished = monotonic()
        if self.timing:
            print("Display {}: buffer {:.3f}s, refresh {:.3f}s".format(
                "full" if full else "partial", converted - started, finished - converted), flush=True)