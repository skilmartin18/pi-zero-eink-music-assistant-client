"""Monitor a Music Assistant player and render its now-playing screen."""

import argparse
import ast
import asyncio
import math
import os
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import socket 

import aiohttp
from dotenv import load_dotenv
from music_assistant_client import MusicAssistantClient
from music_assistant_client.exceptions import InvalidServerVersion, InvalidState, NotConnected, TransportError
from music_assistant_models.errors import AuthenticationFailed, AuthenticationRequired, InvalidToken
from music_assistant_models.player import Player

from generate_ui import render_connection_error, render_now_playing
from render_ui import DisplayOutput, OutputWorker, PreviewOutput


CONNECTION_ERRORS = (TransportError, NotConnected, InvalidState, InvalidServerVersion,
                     aiohttp.ClientError, asyncio.TimeoutError, AuthenticationFailed,
                     AuthenticationRequired, InvalidToken)
RESERVED_PINS = {0, 1, 8, 10, 11, 17, 18, 19, 21, 24, 25}


class ServerConnectionError(Exception):
    pass


class QueueElapsedTracker:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.sample = None

    def elapsed(self, queue, item, player_state):
        elapsed = queue.corrected_elapsed_time
        duration = item.duration
        if (isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0
                and elapsed > duration + 2):
            self.sample = None
            return None

        item_id = getattr(item, "queue_item_id", None)
        raw = getattr(queue, "elapsed_time", None)
        updated = getattr(queue, "elapsed_time_last_updated", None)
        speed = getattr(queue, "playback_speed", None)
        if (not getattr(queue, "flow_mode", False) or not item_id
                or getattr(player_state, "value", player_state) != "playing"
                or getattr(queue.state, "value", queue.state) != "playing"
                or not all(isinstance(value, (int, float)) and math.isfinite(value)
                           for value in (raw, updated, speed)) or speed <= 0):
            self.sample = None
            return elapsed

        now = self.clock()
        key = (getattr(queue, "queue_id", None), item_id, speed)
        if self.sample is None or self.sample[0] != key or self.sample[1] != raw or updated < self.sample[2]:
            self.sample = (key, raw, updated, now, elapsed)
            return elapsed
        _, _, first_updated, first_clock, first_elapsed = self.sample
        if updated - first_updated < 1.5:
            return elapsed
        estimate = first_elapsed + max(0, now - first_clock) * speed
        if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0:
            estimate = min(estimate, duration)
        return estimate


async def server_request(request):
    try:
        return await asyncio.wait_for(request, timeout=15)
    except CONNECTION_ERRORS as error:
        raise ServerConnectionError from error


@asynccontextmanager
async def connect_to_server(client):
    async with AsyncExitStack() as stack:
        await server_request(stack.enter_async_context(client))
        yield client


def format_time(seconds):
    if seconds is None:
        return "unknown"
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return "{}:{:02d}".format(minutes, seconds)


def wifi_signal_dbm(interface="wlan0", path=Path("/proc/net/wireless")):
    try:
        lines = path.read_text().splitlines()[2:]
    except OSError:
        return None
    for line in lines:
        name, separator, values = line.partition(":")
        if not separator or name.strip() != interface:
            continue
        fields = values.split()
        try:
            signal = float(fields[2].rstrip("."))
        except (IndexError, ValueError):
            return None
        if 128 <= signal <= 255:
            signal -= 256
        return signal if -127 <= signal < 0 else None
    return None


@asynccontextmanager
async def listen_for_events(client):
    listener = asyncio.create_task(client.start_listening())
    try:
        await asyncio.sleep(0)
        if listener.done():
            await server_request(listener)
        yield
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)


async def download_cover(session, url, server_url, token, destination):
    url = urljoin(server_url.rstrip("/") + "/", url)
    source = urlsplit(url)
    server = urlsplit(server_url)
    if source.scheme not in ("http", "https"):
        raise ValueError("Unsupported cover art URL")
    same_origin = (source.scheme, source.hostname, source.port) == (server.scheme, server.hostname, server.port)
    headers = {"Authorization": "Bearer " + token} if token and same_origin else {}
    async with session.get(url, headers=headers, allow_redirects=False,
                           timeout=aiohttp.ClientTimeout(total=10)) as response:
        response.raise_for_status()
        if response.status != 200 or not response.content_type.startswith("image/"):
            raise ValueError("Cover art response is not an image")
        image = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            image.extend(chunk)
            if len(image) > 5 * 1024 * 1024:
                raise ValueError("Cover art exceeds 5 MiB")
        if not image:
            raise ValueError("Cover art is empty")
    destination.write_bytes(image)


async def monitor_connection(output_worker):
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
    
    server_url = os.environ.get("MA_URL")
    if not server_url:
        raise SystemExit("Set MA_URL to your Music Assistant server URL, e.g. http://192.168.1.10:8095")
    
    
    player_name = os.environ.get("MA_PLAYER")
    if not player_name:
            player_name = socket.gethostname()
            print(f"Set MA_PLAYER to the Music Assistant Player to read from, e.g. living-room-02. \n Defaulting to machine hostname: {player_name}")
        
    token = os.environ.get("MA_TOKEN")
    if not token:
                raise SystemExit("Set MA_TOKEN based on a Music Assistant Long-lived-access-token")
            
    cover_path = Path(os.environ.get("MA_COVER_PATH", "cover-art.jpg"))
    
    previous_art = None
    elapsed_tracker = QueueElapsedTracker()
    
    async with aiohttp.ClientSession() as session:
        client = MusicAssistantClient(server_url.rstrip("/"), session, token=token)
        async with connect_to_server(client), listen_for_events(client):
            while True:
                
                
                players = await server_request(client.send_command("players/all"))
                
                # make sure we are getting the data from the player 
                matches = [player for player in players
                           if player.get("name", player.get("display_name")) == player_name]
                if not matches:
                    raise SystemExit("No player named {!r} found".format(player_name))
                player = Player.from_dict(matches[0])
                
                
                queue = await server_request(client.player_queues.get_active_queue(player.player_id))
                item = queue.current_item if queue else None
                next_item = queue.next_item if queue else None
                if item:
                    media = item.media_item
                    title = getattr(media, "name", None) or item.name
                    artists = getattr(media, "artists", None) or []
                    artist = ", ".join(artist.name for artist in artists) or None
                    album = getattr(getattr(media, "album", None), "name", None)
                    duration = item.duration
                    elapsed = elapsed_tracker.elapsed(queue, item, player.playback_state)
                    cover_url = client.get_image_url(item.image) if item.image else None
                else:
                    elapsed_tracker.sample = None
                    media = player.current_media
                    title = media.title if media else None
                    artist = media.artist if media else None
                    album = media.album if media else None
                    duration = media.duration if media else None
                    elapsed = player.corrected_elapsed_time
                    cover_url = media.image_url if media else None
                    
    
                artwork = None
                if cover_url:
                    if cover_url != previous_art or not cover_path.is_file():
                        previous_art = None
                        try:
                            await download_cover(session, cover_url, server_url, token, cover_path)
                            previous_art = cover_url
                        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as error:
                            print("Cover download failed: {}".format(error), file=sys.stderr)
                    if previous_art == cover_url:
                        artwork = str(cover_path)
                        
                else:
                    previous_art = None
                    
                
                image = render_now_playing({
                    "player_name": player.name,
                    "wifi_signal_dbm": wifi_signal_dbm(os.environ.get("WIFI_INTERFACE") or "wlan0"),
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "state": player.playback_state,
                    "volume": player.volume_level,
                    "elapsed": elapsed,
                    "duration": duration,
                    "next_title": next_item.name if next_item else None,
                    "artwork": artwork,
                })
                track_identifier = (getattr(media, "uri", None) or
                                    getattr(item, "queue_item_id", None))
                song_key = (track_identifier, title, artist, album) if track_identifier or title else None
                output_worker.submit(image, song_key)
                
                print("Player: {} | State: {} | Volume: {}".format(player.name, player.playback_state, player.volume_level), flush=True)
                print("Song: {} | Artist: {} | Album: {}".format(title or "unknown", artist or "unknown", album or "unknown"), flush=True)
                print("Next: {}".format(next_item.name if next_item else "unavailable"), flush=True)
                print("Time: {} / {} | Cover: {}\n".format(format_time(elapsed), format_time(duration), artwork or "unavailable"), flush=True)
                await asyncio.sleep(1)


async def main(output):
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
    server_url = os.environ.get("MA_URL")
    if not server_url:
        raise SystemExit("Set MA_URL to your Music Assistant server URL, e.g. http://192.168.1.10:8095")
    player_name = os.environ.get("MA_PLAYER") or socket.gethostname()
    async with OutputWorker(output) as output_worker:
        while True:
            try:
                await monitor_connection(output_worker)
            except ServerConnectionError:
                print("{} cannot connect to {}. Retrying in 10 seconds.".format(player_name, server_url),
                      file=sys.stderr, flush=True)
                output_worker.submit(render_connection_error(player_name, server_url), None, urgent=True)
                await asyncio.sleep(10)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test", action="store_true", help="save the UI to ui.png without using the display")
    mode.add_argument("--display", choices=["epd2in9_V2"], default="epd2in9_V2", help="display model (default: epd2in9_V2)")
    parser.add_argument("--power-pin", type=int, help="verified display power-control BCM pin")
    args = parser.parse_args(argv)
    if not args.test and args.power_pin is None:
        parser.error("display mode requires a verified --power-pin")
    return args


def load_display_driver(model, power_pin):
    if model != "epd2in9_V2":
        raise ValueError("Unsupported display model")
    if power_pin not in range(2, 28) or power_pin in RESERVED_PINS:
        raise ValueError("Power pin conflicts with the DAC or display signals")
    config = Path(__file__).resolve().parent / "lib/waveshare_epd/epdconfig.py"
    tree = ast.parse(config.read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "RaspberryPi":
            for statement in node.body:
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "PWR_PIN"
                    for target in statement.targets
                ):
                    if ast.literal_eval(statement.value) != power_pin:
                        raise ValueError("Power pin does not match the local Waveshare RaspberryPi configuration")
                    from lib.waveshare_epd import epd2in9_V2

                    return epd2in9_V2
    raise ValueError("Cannot determine the Waveshare Raspberry Pi power pin")


def create_output(args):
    if args.test:
        return PreviewOutput("ui.png")
    return DisplayOutput(load_display_driver(args.display, args.power_pin))


if __name__ == "__main__":
    args = parse_args()
    try:
        with create_output(args) as output:
            asyncio.run(main(output))
    except KeyboardInterrupt:
        pass