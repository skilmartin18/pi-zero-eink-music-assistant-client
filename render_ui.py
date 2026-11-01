"""Output now-playing images to a PNG or a Waveshare display."""

import asyncio
from pathlib import Path


class OutputWorker:
    def __init__(self, output):
        self.output = output
        self.pending = None
        self.pending_error = None
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

    def submit(self, image, song_key, urgent=False):
        if self.task.done():
            self.task.result()
            raise RuntimeError("Display worker stopped unexpectedly")
        if urgent:
            self.pending_error = (image, song_key, urgent)
            self.pending = None
        else:
            self.pending = (image, song_key, urgent)
        self.wake.set()

    async def run(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            if self.stopping:
                return
            if self.pending_error is not None:
                image, song_key, urgent = self.pending_error
                self.pending_error = None
                if self.pending is not None:
                    self.wake.set()
            else:
                image, song_key, urgent = self.pending
                self.pending = None
            if urgent:
                await asyncio.to_thread(self.output.update, image, song_key, urgent=True)
            else:
                await asyncio.to_thread(self.output.update, image, song_key)


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
        if urgent or (self.has_displayed and song_key == self.previous_song):
            return False
        if image.mode != "1" or image.size != (self.display.height, self.display.width):
            raise ValueError("Display image must be a landscape 1-bit image matching the panel")
        if not self.has_displayed:
            print("Drawing first now-playing image...", flush=True)
            self.display.display_Base(self.display.getbuffer(image))
            print("First display refresh complete.", flush=True)
        else:
            self.display.sleep()
            self.initialized = False
            if self.display.init() != 0:
                raise RuntimeError("Display reinitialization failed")
            self.initialized = True
            self.display.display_Base(self.display.getbuffer(image))
        self.has_displayed = True
        self.previous_song = song_key
        return True