"""Monitor a Music Assistant player and render its now-playing screen."""

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import socket 

import aiohttp
from dotenv import load_dotenv
from music_assistant_client import MusicAssistantClient
from music_assistant_models.player import Player

from now_playing_image import render_now_playing
from now_playing_output import DisplayOutput, OutputWorker, PreviewOutput


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
            await listener
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


async def main(output=None):
    if output is None:
        output = PreviewOutput("now-playing-preview.png")
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
    
    async with aiohttp.ClientSession() as session, OutputWorker(output) as output_worker:
        async with MusicAssistantClient(server_url.rstrip("/"), session, token=token) as client, listen_for_events(client):
            while True:
                
                
                players = await client.send_command("players/all")
                
                # make sure we are getting the data from the player 
                matches = [player for player in players
                           if player.get("name", player.get("display_name")) == player_name]
                if not matches:
                    raise SystemExit("No player named {!r} found".format(player_name))
                player = Player.from_dict(matches[0])
                
                
                queue = await client.player_queues.get_active_queue(player.player_id)
                item = queue.current_item if queue else None
                next_item = queue.next_item if queue else None
                
                if item:
                    media = item.media_item
                    title = getattr(media, "name", None) or item.name
                    artists = getattr(media, "artists", None) or []
                    artist = ", ".join(artist.name for artist in artists) or None
                    album = getattr(getattr(media, "album", None), "name", None)
                    duration = item.duration
                    elapsed = queue.corrected_elapsed_time
                    cover_url = client.get_image_url(item.image) if item.image else None
                else:
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", metavar="PNG", default=None, help="save a preview PNG (default: now-playing-preview.png)")
    mode.add_argument("--display", choices=["epd2in9_V2"], help="render on the selected display")
    parser.add_argument("--power-pin", type=int, help="verified display power-control BCM pin")
    parser.add_argument("--refresh-interval", type=float, default=0, help="minimum seconds between display refreshes (default: 0)")
    args = parser.parse_args(argv)
    if args.display and args.power_pin is None:
        parser.error("--display requires a verified --power-pin")
    if not 0 <= args.refresh_interval < float("inf"):
        parser.error("--refresh-interval must be non-negative")
    return args


def create_output(args):
    if not args.display:
        return PreviewOutput(args.preview or "now-playing-preview.png")
    from display_test import load_driver

    return DisplayOutput(load_driver(args.display, args.power_pin), min_interval=args.refresh_interval)


if __name__ == "__main__":
    args = parse_args()
    try:
        with create_output(args) as output:
            asyncio.run(main(output))
    except KeyboardInterrupt:
        pass