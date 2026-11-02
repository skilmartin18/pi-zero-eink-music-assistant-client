"""Output now-playing images to a PNG or a Waveshare display."""

from pathlib import Path


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
    def __init__(self, driver):
        self.driver = driver
        self.display = driver.EPD()
        self.has_displayed = False
        self.previous_song = None
        self.previous_image = None
        self.showing_error = False
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
        if not self.has_displayed or (not urgent and song_key is not None
                          and song_key != self.previous_song):
            if self.has_displayed:
                self.display.sleep()
                self.initialized = False
                if self.display.init() != 0:
                    raise RuntimeError("Display reinitialization failed")
                self.initialized = True
            self.display.display_Base(self.display.getbuffer(image))
            self.previous_song = None if urgent else song_key
            self.previous_image = image.copy()
            self.showing_error = urgent
        else:
            if urgent or self.showing_error:
                partial_image = image.copy()
            else:
                partial_image = self.previous_image.copy()
                for region in ((0, 0, image.width - 168, 16), (0, 96, image.width, image.height)):
                    partial_image.paste(image.crop(region), region)
            if partial_image.tobytes() == self.previous_image.tobytes():
                self.showing_error = urgent
                return False
            self.display.display_Partial(self.display.getbuffer(partial_image))
            self.previous_image = partial_image
            self.showing_error = urgent
        self.has_displayed = True
        return True