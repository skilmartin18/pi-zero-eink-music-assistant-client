# Music Assistant player data

This Python interface polls one Music Assistant player once per second and downloads its current cover art. It can save a now-playing PNG preview or show it on a supported e-paper display. It does not control playback.

## E-paper text test

The standalone text test draws a black-and-white Pillow image. It does not connect to Music Assistant. Its display mode still uses the separate `e-Paper` checkout, while now-playing display mode uses the local driver in `lib`. On the Pi, install the display dependencies in the Python environment used to run it:

```sh
python3 -m pip install -r requirements.txt
python3 display_test.py --preview /tmp/epaper-preview.png
```

Open the PNG to inspect the design without a display or GPIO libraries. For preview-only use on another computer, installing `Pillow` alone is sufficient. `--text`, `--font`, `--font-size`, `--width`, and `--height` customize the preview; its default 296 x 128 landscape size is only a provisional 2.9-inch reference. Longer text is shortened to fit. The renderer is separate from the display driver so future layouts can use the same preview process.

### Before using the display

Identify the exact Waveshare panel and revision first. The hardware test currently supports only the **2.9-inch V2 monochrome** driver `epd2in9_V2`; do not use it for another panel. The local Waveshare Raspberry Pi backend uses BCM GPIO22 (physical header pin 15) for power control instead of GPIO18, which conflicts with the PCM5102A I2S clock.

With power disconnected, check the panel/HAT documentation and actual wiring. Confirm that GPIO22 is connected to a suitable power-control input and that the display's power supply meets its specifications. Do not assume a GPIO pin can power the display directly. For now-playing, update `PWR_PIN` in the `RaspberryPi` class in `lib/waveshare_epd/epdconfig.py` if a different power-control pin is required. The now-playing app does not check this setting before importing the driver, which can claim GPIO pins. Ensure it does not conflict with GPIO18, the DAC's GPIO19 and GPIO21, the driver's SPI and control pins, or other devices. The standalone display test uses its own backend configuration under `e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epdconfig.py`; verify that setting separately.

The driver files in `lib/waveshare_epd` come from [Waveshare's e-Paper repository](https://github.com/waveshare/e-Paper), revision `d28b3c4cfc78eef1c6cd6e6e3013654e449d2864`. Their original license notices are retained in the source files; the Raspberry Pi power pin has been changed from GPIO18 to GPIO22.

On the Pi, enable SPI using the appropriate DietPi configuration and confirm `/dev/spidev0.0` exists and the user has permission to access SPI and GPIO. The requirements include `spidev`, `gpiozero` and `RPi.GPIO` as a GPIO backend. Package availability and native builds on the original ARMv6 Pi are unverified; system packages may be needed if pip installation fails. Do not run with elevated privileges merely to bypass device permissions.

Once the V2 model and power wiring are verified, use the configured BCM pin:

```sh
python3 display_test.py --display epd2in9_V2 --power-pin 22
```

This initializes the display, draws once, and puts it to sleep. E-paper normally retains the image after sleep. If the test reports a pin mismatch, recheck both wiring and the backend setting; if initialization hangs, check the exact panel revision, SPI, busy pin and wiring. This test and the display's compatibility with the DAC have not yet been verified on the Pi. Confirm audio remains operational after the display test.

The standalone text test above requires `display_render.py`; if that file has been removed, use the now-playing preview and display modes below instead.

### Now-playing image

`generate_ui.py` provides a separate Pillow renderer. It accepts a dictionary using the values already collected by `main.py`:

```python
from generate_ui import render_now_playing
from main import wifi_signal_dbm

image = render_now_playing({
    "player_name": player.name,
    "wifi_signal_dbm": wifi_signal_dbm(),
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
image.save("now-playing-preview.png")
```

The returned image is a 296 x 128 monochrome Pillow image by default; `width`, `height`, `font_path`, and `bold_font_path` are optional renderer arguments. Unknown fields and missing artwork are supported. `wifi_signal_dbm` is the local Pi's signal level in dBm, not the Music Assistant server's signal. `main.py` reads `/proc/net/wireless` for `wlan0` by default; set `WIFI_INTERFACE` in `.env` for a different interface. When the signal is unavailable, the Wi-Fi icon is crossed out; this can mean the system does not report signal data, not necessarily that it is disconnected. The display labels both paused and idle states as paused.

Text uses Pixelify Sans when it contains all the required characters. If any character is missing, the entire text field uses Noto Sans CJK instead. The installer installs the fallback fonts; for a manual setup, run `sudo apt install fonts-noto-cjk` on the Pi and install the Python requirements. The renderer expects the Noto font collections at `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` and `/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc`. Characters not included in Noto may still appear as missing glyphs.

Run the player in test mode to save the UI to `ui.png` without using the display:

```sh
python3 main.py --test
```

After verifying the exact V2 panel and the power wiring described above, run it on the display:

```sh
python3 main.py
```

Display mode uses `epd2in9_V2` and the power pin configured in the driver. It performs an initial full refresh and another full refresh when the song changes. Other image changes, including connection errors and recovery, can use partial refreshes. The driver applies the partial waveform to the whole panel. Partial refreshes may cause ghosting until the next full refresh. Test mode updates `ui.png` as player data changes.

The Party QR code is disabled by default. To fetch and display the guest join URL, enable guest access in the Party plugin and run `python3 main.py --party-qr`, or use `python3 main.py --test --party-qr` for a preview. Song details are shortened to make room. The QR code is omitted if the URL is too long to fit the display; small QR modules may be difficult to scan on the physical panel.

The clock beside the Wi-Fi icon shows the device's local time to the minute. It defaults to 24-hour time; use `python3 main.py --time-format 12` for 12-hour time with AM/PM, or `--time-format 24` to select 24-hour time explicitly. Set the Pi's system time and time zone to ensure the clock is correct. The renderer also accepts `time_format="12"` or `time_format="24"` as an optional argument.

Display updates run in the player loop, which targets a one-second polling interval. Network requests, rendering, and display updates all count toward that interval; work taking longer than one second delays the next poll. Stop with Ctrl+C to put the panel to sleep.

## Console script using the official client

The separate `main.py` script uses `music-assistant-client` and prints the selected player's song, state, volume, progress, and downloaded cover path. Install its dependencies in your Python environment:

```sh
python3 -m pip install -r requirements.txt
```

Put these settings in a `.env` file next to the script:

```dotenv
MA_URL=http://YOUR_MUSIC_ASSISTANT_IP:8095
MA_PLAYER=marceline
MA_TOKEN=YOUR_LONG_LIVED_TOKEN
```

Run `python3 main.py` from the script's directory after verifying the wiring, or use the Python executable from your virtual environment. Press Ctrl+C to stop. Existing environment variables take precedence over `.env`; `MA_COVER_PATH` changes the cover filename (default `cover-art`). Keep `.env` private and do not commit it. The client and its dependencies must be installable on the Pi; compatibility with the actual Pi and Music Assistant server has not yet been verified.

### Start automatically on the Pi

For interactive setup on a fresh DietPi Raspberry Pi with systemd, run from the project checkout:

```sh
sudo python3 install.py
```

The installer asks for the hostname, Music Assistant URL, token, player name, Wi-Fi interface, and Snapclient settings. Snapclient installation defaults to yes, and its server defaults to the Music Assistant URL's hostname. The service runs as the invoking sudo user, or root when run directly as root. It creates a new `.env`, replacing any existing file; the token prompt is hidden and the file is readable only by the service user. The checkout must be writable by that user. It appends the PCM5102A overlay and SPI settings to `/boot/firmware/config.txt`, installs dependencies into `.venv`, and generates a service using the actual checkout path. It updates `/etc/hostname` and the `127.0.1.1` entry in `/etc/hosts`, backs up the hosts and boot configuration files, and enables the service for boot. Snapclient setup replaces `/etc/default/snapclient`. A non-root service user is added to the existing `spi` and `gpio` groups. Reboot to apply the hardware, hostname, and group changes.

Confirm the V2 display model and wiring described above before enabling the display service. The hardware guide also describes a display tested with the V3 driver; the application currently uses V2. The installer does not change the driver or verify the hardware, and has not yet been tested on a Pi. Package availability can vary on ARMv6 and different OS releases.

For manual installation, the included `pi-music-player.service` starts the display at boot and restarts it five seconds after a failure. Its paths currently assume `/opt/pi-eink-music-player` and it runs as root unless you add a `User=` setting. Update the paths and set an appropriate non-root user before installing it. Create a virtual environment on the Pi:

```sh
cd /opt/pi-eink-music-player
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Create the `.env` file described above and make sure the service user can read it, write in the project directory, and access the display devices. Then enable and start the service:

```sh
sudo cp pi-music-player.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-music-player.service
sudo systemctl status pi-music-player.service
```

View logs with `journalctl -u pi-music-player.service -f`. After changing the installed unit, run `sudo systemctl daemon-reload` and `sudo systemctl restart pi-music-player.service`.

`MA_PLAYER` is optional: if unset or empty, the script uses the machine's hostname as the Music Assistant player name. The name must match the name shown in Music Assistant.

If the server connection fails or is lost, the script reports the error in the console and retries 10 seconds after each failed attempt. Test mode also writes the connection error to `ui.png`; the e-paper display is not refreshed for connection errors and retains its previous image. Server connection and polling requests time out after 15 seconds.

If multiple players have the same name, the console script selects the first one returned by Music Assistant on each poll. If their order changes, the selected player may change.

When a playing queue repeatedly refreshes its timestamp without advancing its elapsed value, the script estimates progress locally until the server reports a different position. The estimate resets on pause, track change or reconnection, and is limited to the track duration. This is a workaround for stale server data, not an authoritative playback position: it can drift, jump back on pause, or be wrong after a seek or skip when the server does not report a new position. Server positions more than two seconds beyond the track duration are shown as unknown until valid data returns.

The remainder of this guide describes the separate `music_assistant_player.py` interface, which connects directly using `aiohttp`.

## Setup on the Pi

The code requires Python 3.11 or newer and `aiohttp`. On Debian, Raspberry Pi OS, or DietPi, install the distribution package when available:

```sh
sudo apt update
sudo apt install python3-aiohttp
python3 --version
python3 -c 'import aiohttp; print(aiohttp.__version__)'
```

The original Pi Zero uses ARMv6; package and Python availability depend on the installed OS. This code has not yet been verified on the Pi. Music Assistant server API schema 27 or later is required; schema 28 and later also require authentication.

Create a long-lived access token in Music Assistant under **Settings > Profile**, and set these variables in the shell that will run your program:

```sh
export MA_URL='http://YOUR_MUSIC_ASSISTANT_IP:8095'
export MA_PLAYER='marceline'
export MA_TOKEN='YOUR_LONG_LIVED_TOKEN'
```

Keep the token private. On an untrusted network use HTTPS to avoid sending it in plaintext. Do not commit the token to the repository. Use a URL reachable from the Pi, not necessarily the Snapcast server address.

## Use from Python

```python
import asyncio
import os

from music_assistant_player import MusicAssistantPlayer


async def main():
    player = MusicAssistantPlayer(
        server_url=os.environ["MA_URL"],
        player_name=os.environ.get("MA_PLAYER", "marceline"),
        token=os.environ.get("MA_TOKEN"),
        artwork_path="/tmp/music-assistant-cover",
    )
    async for snapshot in player.updates():
        print(snapshot, flush=True)


asyncio.run(main())
```

Run the consuming program from this directory, or make this directory available on `PYTHONPATH`. The `PlayerSnapshot` fields are `player_name`, `player_id`, `connected`, `available`, `state`, `title`, `artist`, `album`, `volume`, `elapsed`, `duration`, `artwork_path`, and `error`. Volume is the selected player's 0-100 value; elapsed and duration are seconds. Unknown fields are `None`. Artwork retains its original image format even though the configured filename has no extension.

The cover path is `None` until the current image has downloaded, and when there is no image or a download fails. The file may still exist from an earlier track: only use it when the current snapshot reports its path. Downloads are limited to 5 MiB and retried after failure. The default polling interval is one second; it may be extended by a slow server or consumer.

Player names must match exactly and uniquely. Missing or duplicate names, unsupported server versions, and authentication failures raise `ConfigurationError`. Network outages yield a disconnected snapshot with no stale track information and retry with backoff. Stop consuming and close the async generator to clean up its connection.

Snapcast commonly uses flow mode, so track metadata and timing come from the selected player's active Music Assistant queue when available. Otherwise, they come from the player's current media. Some sources do not provide every field. This implementation retrieves the server's player list to identify a unique name, but only publishes data for the configured player.

## Verify

Run tests with `python3 -m unittest discover -s tests -v`. On the Pi, compare the snapshots and cover with Music Assistant while playing, pausing, seeking, changing tracks and changing volume. Also try a second player, a grouped Snapcast player and a server restart. Actual Music Assistant version, Pi compatibility and grouped playback still need live verification.