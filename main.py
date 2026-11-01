"""Monitor a Music Assistant player and render its now-playing screen."""

import argparse
import asyncio
import math
import os
import sys
import time
from contextlib import AsyncExitStack
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


# Used to track the timeelapsed during a song due to a current bug where after playing/pausing 
# the API was no longer updating the elapsed time
class QueueElapsedTracker:
    
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.sample = None

    def elapsed(self, queue, item, player_state):
        
        corrected_elapsed_time = queue.corrected_elapsed_time
        track_duration = item.duration
        

        queue_item_id = getattr(item, "queue_item_id", None)
        elapsed_time = getattr(queue, "elapsed_time", None)
        time_last_updated = getattr(queue, "elapsed_time_last_updated", None)
        playback_speed = getattr(queue, "playback_speed", None)
        
        # is there something playing (elapsed should be advancing)
        is_playing = (
            queue_item_id
            and getattr(player_state, "value", player_state) == "playing"
            and getattr(queue.state, "value", queue.state) == "playing"
        )
        
        # are the vals valid
        has_valid_timing = (
            is_playing
            and all(isinstance(value, (int, float)) and math.isfinite(value)
                    for value in (elapsed_time, time_last_updated, playback_speed))
        )
        
        if not has_valid_timing:
            self.sample = None
            return corrected_elapsed_time


        current_monotonic_time = self.clock()
        playback_details = (getattr(queue, "queueid", None), queue_item_id, playback_speed)
        
        # have the details of the stream changed? if theyre changing, then we are all good and can 
        # return the default of corrected_elapsed_time
        if (self.sample is None
                or self.sample["playback_details"] != playback_details
                or self.sample["elapsed_time"] != elapsed_time
                or time_last_updated < self.sample["time_last_updated"]):
            
            
            self.sample = {
                "playback_details": playback_details,
                "elapsed_time": elapsed_time,
                "time_last_updated": time_last_updated,
                "monotonic_time": current_monotonic_time,
                "corrected_elapsed_time": corrected_elapsed_time,
            }
            
            return corrected_elapsed_time
        
        
        # if we are here, we are experiencing the bug
        
        baseline_time_last_updated = self.sample["time_last_updated"]
        baseline_monotonic_time = self.sample["monotonic_time"]
        baseline_corrected_elapsed_time = self.sample["corrected_elapsed_time"]
        
        # 2 seconds worth of updates until we consider it actually stale
        if time_last_updated - baseline_time_last_updated < 2:
            return corrected_elapsed_time
        
        # estimate the elpased time based on our clock
        estimated_elapsed_time = baseline_corrected_elapsed_time + max(0, current_monotonic_time - baseline_monotonic_time) * playback_speed
        
    
        return min(estimated_elapsed_time, track_duration)


async def server_request(request):
    return await asyncio.wait_for(request, timeout=15)


def format_time(seconds):
    if seconds is None:
        return "unknown"
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return "{}:{:02d}".format(minutes, seconds)


def wifi_signal_dbm(interface="wlan0", path=Path("/proc/net/wireless")):
    
    # Skipheader lines
    try:
        lines = path.read_text().splitlines()[2:]
    except OSError:
        return None
    
    for line in lines:
        # Find the row for the requested interface
        name, separator, values = line.partition(":")
        
        if not separator or name.strip() != interface:
            continue
        
        fields = values.split()
        try:
            # The third field is signal level; Linux may append a dot to it.
            signal = float(fields[2].rstrip("."))
        except (IndexError, ValueError):
            return None
        
        # Some drivers report negative dBm values as unsigned 8-bit numbers.
        if 128 <= signal <= 255:
            signal -= 256
            
        # Reject values outside the expected negative dBm range.
        return signal if -127 <= signal < 0 else None
    return None


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


async def super_loop(output_worker, test_mode=False):
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
    
    async with aiohttp.ClientSession() as session, AsyncExitStack() as connection_stack:
        client = MusicAssistantClient(server_url.rstrip("/"), session, token=token)
        await server_request(connection_stack.enter_async_context(client))
        listener = asyncio.create_task(client.start_listening())
        
        
        try:
            await asyncio.sleep(0)
            

            while True:
                
                
                players = await server_request(client.send_command("players/all"))
                
                # make sure we are getting the data from the correct player 
                matches = [player for player in players
                           if player.get("name", player.get("display_name")) == player_name]
                if not matches:
                    raise SystemExit("No player named {!r} found".format(player_name))
                player = Player.from_dict(matches[0])
                
                
                # get the deets from the player
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
             
              
                
                # pull the artwork if we dont already have it
                artwork = None
                if cover_url:
                    if cover_url != previous_art:
                        previous_art = None
                        try:
                            await download_cover(session, cover_url, server_url, token, cover_path)
                            previous_art = cover_url
                        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as error:
                            print("Cover download failed: {}".format(error), file=sys.stderr)
                    if cover_url == previous_art:
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
                
             
                elapsed_second = (
                    max(0, int(elapsed))
                    if isinstance(elapsed, (int, float)) and math.isfinite(elapsed)
                    else None
                )
                track_duration = (
                    duration if isinstance(duration, (int, float)) and math.isfinite(duration)
                    else None
                )
                playback_state = getattr(player.playback_state, "value", player.playback_state)
                song_key = (title, artist, album, elapsed_second, track_duration, playback_state)
                
                output_worker.submit(image, song_key)
                
                if test_mode:
                    print("Player: {} | State: {} | Volume: {}".format(player.name, player.playback_state, player.volume_level), flush=True)
                    print("Song: {} | Artist: {} | Album: {}".format(title or "unknown", artist or "unknown", album or "unknown"), flush=True)
                    print("Next: {}".format(next_item.name if next_item else "unavailable"), flush=True)
                    print("Time: {} / {} | Cover: {}\n".format(format_time(elapsed), format_time(duration), artwork or "unavailable"), flush=True)
                
                await asyncio.sleep(1)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)


async def main(output, test_mode=False):
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
    
    # get these here for error message
    server_url = os.environ.get("MA_URL")
    if not server_url:
        raise SystemExit("Set MA_URL to your Music Assistant server URL, e.g. http://192.168.1.10:8095")
    player_name = os.environ.get("MA_PLAYER") or socket.gethostname()
    
    # output worker creates the pngs and renders to display
    async with OutputWorker(output) as output_worker:
        while True:
            try:
                await super_loop(output_worker, test_mode=test_mode)
            except CONNECTION_ERRORS as error:
                
                # if we can connect submit the error message to the worker
                error_message = str(error) or type(error).__name__
                print("{}: {}. Retrying in 10 seconds.".format(type(error).__name__, error_message),
                      file=sys.stderr, flush=True)
                error_key = ("connection_error", type(error).__name__, error_message)
                output_worker.submit(render_connection_error(player_name, server_url, error_message), error_key, urgent=True)
                await asyncio.sleep(10)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="save the UI to ui.png without using the display")
    return parser.parse_args(argv)


# we using the real thing or just making pngs
def create_output(args):
    if args.test:
        return PreviewOutput("ui.png")
    from lib.waveshare_epd import epd2in9_V2

    return DisplayOutput(epd2in9_V2)


if __name__ == "__main__":
    args = parse_args()
    try:
        with create_output(args) as output:
            asyncio.run(main(output, test_mode=args.test))
    except KeyboardInterrupt:
        pass