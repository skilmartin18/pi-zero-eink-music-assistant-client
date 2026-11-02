"""Monitor a Music Assistant player and render its now-playing screen."""

import argparse
import math
import os
import sys
import time
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urljoin, urlsplit
import socket 

import aiohttp
from dotenv import load_dotenv
from music_assistant_client.exceptions import InvalidServerVersion, InvalidState, NotConnected, TransportError
from music_assistant_models.errors import (AuthenticationFailed, AuthenticationRequired,
                                         InsufficientPermissions, InvalidCommand, InvalidToken,
                                         ProviderUnavailableError)
from music_assistant_models.player import Player

from generate_ui import render_connection_error, render_now_playing
from music_assistant_client_synchronous import MusicAssistantClientSynchronous
from render_ui import DisplayOutput, PreviewOutput


CONNECTION_ERRORS = (TransportError, NotConnected, InvalidState, InvalidServerVersion,
                     aiohttp.ClientError, TimeoutError, AuthenticationFailed,
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


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def download_cover(url, server_url, token, destination):
    url = urljoin(server_url.rstrip("/") + "/", url)
    
    headers = {"Authorization": "Bearer " + token} 
    request = Request(url, headers=headers)
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        if response.status != 200 or not response.headers.get_content_type().startswith("image/"):
            raise ValueError("Cover art response is not an image")
        image = bytearray()
        while chunk := response.read(65536):
            image.extend(chunk)
            if len(image) > 5 * 1024 * 1024:
                raise ValueError("Cover art exceeds 5 MiB")
        if not image:
            raise ValueError("Cover art is empty")
    destination.write_bytes(image)


def super_loop(output, test_mode=False, party_qr=False, display_timing=False):
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
    previous_track = None
    received_data = False
    elapsed_tracker = QueueElapsedTracker()
    
    with MusicAssistantClientSynchronous(server_url.rstrip("/"), token) as client:
            while True:
                iteration_start = time.monotonic()
                
                
                players = client.send_command("players/all")
                players_end = time.monotonic()
                
                # make sure we are getting the data from the correct player 
                matches = [player for player in players
                           if player.get("name", player.get("display_name")) == player_name]
                if not matches:
                    raise SystemExit("No player named {!r} found".format(player_name))
                player = Player.from_dict(matches[0])
                
                
                # get the deets from the player
                queue = client.get_active_queue(player.player_id)
                queue_end = time.monotonic()
                if not received_data:
                    print("Receiving Music Assistant data.", flush=True)
                    received_data = True
                
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
                    song_key = (getattr(media, "uri", None)
                                or ((title, artist, album) if title else None)
                                or getattr(item, "queue_item_id", None))
                    previous_track = (title, artist, album, duration, cover_url, song_key)
                else:
                    elapsed_tracker.sample = None
                    title, artist, album, duration, cover_url, song_key = (
                        previous_track if previous_track is not None
                        else (None, None, None, None, None, None)
                    )
                    elapsed = player.corrected_elapsed_time
             
              
                
                # pull the artwork if we dont already have it
                artwork = None
                if cover_url:
                    if cover_url != previous_art:
                        previous_art = None
                        try:
                            download_cover(cover_url, server_url, token, cover_path)
                            previous_art = cover_url
                        except (OSError, ValueError) as error:
                            print("Cover download failed: {}".format(error), file=sys.stderr)
                    if cover_url == previous_art:
                        artwork = str(cover_path)
                else:
                    previous_art = None

                party_url = None
                party_error = None
                party_start = time.monotonic()
                if party_qr:
                    try:
                        party_url = client.send_command("party/url")
                    except (InvalidCommand, ProviderUnavailableError, InsufficientPermissions) as error:
                        party_error = str(error)
                render_start = time.monotonic()
                    
                # create the image of the ui
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
                    "party_url": party_url,
                })
                
                 # output ui to image or display
                display_start = time.monotonic()
                output.update(image, song_key)
                if display_timing:
                    display_end = time.monotonic()
                    print("Loop: data/render {:.3f}s, output {:.3f}s".format(
                        display_start - iteration_start, display_end - display_start), flush=True)
                    print("Timing: players {:.3f}s, queue {:.3f}s, party {:.3f}s, render {:.3f}s".format(
                        players_end - iteration_start, queue_end - players_end,
                        render_start - party_start, display_start - render_start), flush=True)
                
                if test_mode:
                    print("Player: {} | State: {} | Volume: {}".format(player.name, player.playback_state, player.volume_level), flush=True)
                    print("Song: {} | Artist: {} | Album: {}".format(title or "unknown", artist or "unknown", album or "unknown"), flush=True)
                    print("Next: {}".format(next_item.name if next_item else "unavailable"), flush=True)
                    if party_qr:
                        print("Party URL: {}".format(party_url or ("unavailable ({})".format(party_error) if party_error else "unavailable")), flush=True)
                    print("Time: {} / {} | Cover: {}\n".format(format_time(elapsed), format_time(duration), artwork or "unavailable"), flush=True)
                
                time.sleep(max(0, 1 - (time.monotonic() - iteration_start)))


def main(output, test_mode=False, party_qr=False, display_timing=False):
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
    
    # get these here for error message
    server_url = os.environ.get("MA_URL")
    if not server_url:
        raise SystemExit("Set MA_URL to your Music Assistant server URL, e.g. http://192.168.1.10:8095")
    player_name = os.environ.get("MA_PLAYER") or socket.gethostname()
    
    while True:
        try:
            super_loop(output, test_mode=test_mode, party_qr=party_qr, display_timing=display_timing)
        except CONNECTION_ERRORS as error:
            error_message = str(error) or type(error).__name__
            print("{}: {}. Retrying in 10 seconds.".format(type(error).__name__, error_message),
                  file=sys.stderr, flush=True)
            error_key = ("connection_error", type(error).__name__, error_message)
            output.update(render_connection_error(player_name, server_url, error_message), error_key, urgent=True)
            time.sleep(10)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="save the UI to ui.png without using the display")
    parser.add_argument("--party-qr", action="store_true", help="show the Party guest join QR code")
    parser.add_argument("--display-timing", action="store_true", help="print display and loop timings")
    return parser.parse_args(argv)


# we using the real thing or just making pngs
def create_output(args):
    if args.test:
        return PreviewOutput("ui.png")
    from lib.waveshare_epd import epd2in9_V2

    return DisplayOutput(epd2in9_V2, timing=args.display_timing)


if __name__ == "__main__":
    args = parse_args()
    try:
        with create_output(args) as output:
            main(output, test_mode=args.test, party_qr=args.party_qr, display_timing=args.display_timing)
    except KeyboardInterrupt:
        pass