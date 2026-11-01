"""Output now-playing images to a PNG or a Waveshare display."""

import time
from pathlib import Path


class PreviewOutput:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        print("Output mode: preview ({})".format(self.path), flush=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def update(self, image):
        image.save(self.path)


class DisplayOutput:
    def __init__(self, driver, min_interval=0, full_refresh_every=10, clock=time.monotonic):
        if min_interval < 0 or full_refresh_every < 1:
            raise ValueError("Refresh interval must be non-negative and full refresh count must be positive")
        self.driver = driver
        self.display = driver.EPD()
        self.min_interval = min_interval
        self.full_refresh_every = full_refresh_every
        self.clock = clock
        self.previous_frame = None
        self.last_refresh = None
        self.partial_count = 0
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

    def update(self, image):
        if image.mode != "1" or image.size != (self.display.height, self.display.width):
            raise ValueError("Display image must be a landscape 1-bit image matching the panel")
        frame = image.tobytes()
        now = self.clock()
        if self.last_refresh is not None and now - self.last_refresh < self.min_interval:
            return False
        if self.previous_frame is None:
            print("Drawing first now-playing image...", flush=True)
            self.display.display_Base(self.display.getbuffer(image))
            print("First display refresh complete.", flush=True)
            self.partial_count = 0
        elif self.partial_count >= self.full_refresh_every:
            self.display.sleep()
            self.initialized = False
            if self.display.init() != 0:
                raise RuntimeError("Display reinitialization failed")
            self.initialized = True
            self.display.display_Base(self.display.getbuffer(image))
            self.partial_count = 0
        else:
            self.display.display_Partial(self.display.getbuffer(image))
            self.partial_count += 1
        self.previous_frame = frame
        self.last_refresh = self.clock()
        return True