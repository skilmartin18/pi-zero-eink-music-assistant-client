"""Output now-playing images to a PNG or a Waveshare display."""

import asyncio
import time
from pathlib import Path


class OutputWorker:
    def __init__(self, output):
        self.output = output
        self.pending = None
        self.wake = asyncio.Event()
        self.stopping = False
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.run())
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.stopping = True
        self.wake.set()
        await self.task
        return False

    def submit(self, image, song_key):
        if self.task.done():
            self.task.result()
            raise RuntimeError("Display worker stopped unexpectedly")
        self.pending = (image, song_key)
        self.wake.set()

    async def run(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            if self.stopping:
                return
            image, song_key = self.pending
            self.pending = None
            await asyncio.to_thread(self.output.update, image, song_key)


class PreviewOutput:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        print("Output mode: preview ({})".format(self.path), flush=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def update(self, image, song_key=None):
        image.save(self.path)


class DisplayOutput:
    def __init__(self, driver, min_interval=0, clock=time.monotonic):
        if min_interval < 0:
            raise ValueError("Refresh interval must be non-negative")
        self.driver = driver
        self.display = driver.EPD()
        self.min_interval = min_interval
        self.clock = clock
        self.previous_frame = None
        self.previous_song = None
        self.last_refresh = None
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

    def update(self, image, song_key=None):
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
        elif song_key is not None and song_key != self.previous_song:
            self.display.sleep()
            self.initialized = False
            if self.display.init() != 0:
                raise RuntimeError("Display reinitialization failed")
            self.initialized = True
            self.display.display_Base(self.display.getbuffer(image))
        else:
            self.display.display_Partial(self.display.getbuffer(image))
        self.previous_frame = frame
        if song_key is not None:
            self.previous_song = song_key
        self.last_refresh = self.clock()
        return True