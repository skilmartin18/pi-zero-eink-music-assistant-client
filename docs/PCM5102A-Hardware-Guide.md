# PCM5102A I²S DAC as a Snapcast Client on a Pi Zero W (DietPi)

A working setup for using a PCM5102A DAC on a Raspberry Pi Zero W as a Snapcast player for Music Assistant, plus the problems we hit and what fixed them.

**What was used**
- Raspberry Pi Zero W (original, ARMv6) running DietPi
- PCM5102A breakout with a 3.5 mm output jack
- Music Assistant with Snapcast, Snapclient on the Pi
- Hi-Fi line input (red/white RCA) and an oscilloscope for debugging

Based on [Raspberry Pi HQ Audio, PCM5102 and MPD](https://www.instructables.com/Raspberry-Pi-HQ-Audio-PCM5102-and-MPD/). We used its hardware and overlay steps and skipped the MPD part.

## 1. Wiring

| DAC pin | Pi GPIO | Header pin |
|---|---|---|
| `BCK` | GPIO18 (PCM_CLK) | 12 |
| `LCK` | GPIO19 (PCM_FS) | 35 |
| `DIN` | GPIO21 (PCM_DOUT) | 40 |
| `GND` | GND | 6 or 14 |
| `VCC` | 5 V | 2 or 4 |

- `DIN` goes to GPIO21 (data out), not GPIO20 (data in).
- **Power `VCC` from 5 V, as the Instructable does.** This breakout has on-board regulation. The bare PCM5102A chip is a 3.3 V part, but running this module's `VCC` from 3.3 V made it unreliable for us.
- **Tie `SCK` to ground** with the module's top solder bridge, as in the Instructable. It is not wired to the Pi. Check with the Pi off that `SCK` and `GND` have continuity, and re-solder if the connection is poor.
- The four underside bridges set filter, de-emphasis, mute and format. The TI datasheet defines the logic levels, but not which pad does what on a clone board, so match your board to the Instructable's photos. Don't guess.
- Output is line level. Use it on a line input, not phono or passive speakers.
- Solder every joint properly. Bad joints on the output connections gave a gritty sound, audio that cut out when the jack was touched, and buzz near the board.

## 2. Pi configuration

In `/boot/firmware/config.txt`:

```ini
# dtparam=audio=on
dtoverlay=hifiberry-dac
```

- Use only one DAC overlay.
- DietPi's soundcard-reset routine can remove an active `dtoverlay=hifiberry-*` line while leaving its comment. If the card disappears, check that the line is still there.

Reboot, then verify:

```sh
aplay -l
```

You should see the card `sndrpihifiberry` (`snd_rpi_hifiberry_dac`).

## 3. Snapclient

- Use the stream port, normally **1704**. Port 1780 is the web UI, and pointing the client at it gave an immediate EOF.
- List outputs with `snapclient -l`. We used `plughw:CARD=sndrpihifiberry,DEV=0`. The direct device is `hw:CARD=sndrpihifiberry,DEV=0`.
- Options are in `/etc/default/snapclient`.

## 4. Testing without Snapcast

```sh
sudo systemctl stop snapclient
speaker-test -D hw:CARD=sndrpihifiberry,DEV=0 -c 2 -t sine -f 440 -l 1
sudo systemctl start snapclient
```

- The direct `hw:` device needs stereo, so `-c 1` fails.
- `speaker-test` pauses between channels, so a bursty scope trace is not a fault by itself.
- For a continuous tone, generate a WAV with `sox -n -r 48000 -c 2 /tmp/tone.wav synth 30 sine 440` and play it with `aplay -D hw:CARD=sndrpihifiberry,DEV=0 /tmp/tone.wav`.
- If `scp` complains about `sftp-server`, use `scp -O`.

## 5. Problems and what they meant

| Symptom | Cause / finding |
|---|---|
| Snapclient: EOF on connect | Wrong port (1780 instead of 1704) |
| `PCM device ... not found` | The overlay line had disappeared from `config.txt` |
| Gritty tone; cuts out when the jack is touched; buzz when a hand is near | Poor solder joints or an open ground/signal connection. Resoldering fixed the touch sensitivity |
| Direct WAV also crunchy | Not Snapcast. The fault was downstream of the stream |
| Scope looks clean but sound is bad | A clean trace at one probe point doesn't prove the Hi-Fi gets clean stereo. Check cable, input and grounds |
| `No chunk received for 5000ms. Closing ALSA.` | No audio chunks for 5 s, so Snapclient closes the device. It is a stream gap, not a DAC open failure |
| `pShortBuffer` / `pMiniBuffer` log lines | Hard-sync corrections for timing drift, not ALSA errors |
| Playback stops and only a DAC power cycle recovers it | Fixed by 5 V `VCC` and a solid `SCK`-to-`GND` connection (see section 6) |

Scope tips:
- Probe tip on the RCA centre pin, ground clip on the outer shell or DAC ground, never on a signal pin.
- A mains-earthed scope can create a ground loop when the Hi-Fi is connected too.
- Don't probe speaker terminals.

## 6. The final fix

Playback stopped at random and only a power cycle of the DAC brought it back. Rebooting just the Pi did nothing, because the Pi's 5 V stays on and the DAC kept its bad state. It became reliable after:

1. **Powering the module's `VCC` from 5 V** (it had been moved to 3.3 V), and
2. **Grounding `SCK`** solidly, as in the Instructable.

I haven't confirmed the exact internal cause. It is consistent with the chip being unable to settle its internal clock from `BCK` when the module is under-powered or `SCK` isn't reliably tied low, which fails whenever the clocks stop and restart.

If the DAC latches again, don't reboot first. Run `speaker-test` and use the scope:
- `BCK` (about 3 MHz) and `LCK` (48 kHz) present but no output: the DAC is stuck. Check its supply and `XSMT`.
- No clocks: the problem is on the Pi side.
- Also try a separate clean 5 V supply for the DAC with shared ground.

## Final working setup

- `BCK` to GPIO18, `LCK` to GPIO19, `DIN` to GPIO21, `GND` to GND, `VCC` to 5 V
- `SCK` tied to ground on the module
- `dtoverlay=hifiberry-dac`, with `dtparam=audio=on` commented out
- Snapclient on port 1704, output `plughw:CARD=sndrpihifiberry,DEV=0`

## 7. 2.9-inch e-ink display

We added a DIY MORE 2.9-inch black-and-white SPI display, 296 × 128 pixels, with `FPC-7519 rev.b TEST4` on the ribbon. The seller lists its controller as SSD1680Z. The Waveshare 2.9-inch V3 driver worked for our display after correcting a misplaced GPIO connection; the ribbon marking alone does not guarantee that another panel uses the same driver.

### Wiring

Shut down and unplug the Pi before changing connections. These pin numbers are **physical header pins**, not GPIO numbers. This display uses 3.3 V, unlike the 5 V DAC module above.

| Display pin | Pi GPIO / connection | Header pin |
|---|---|---|
| `GND` | GND | 25 |
| `VCC` | 3.3 V | 1 |
| `SCL` | GPIO11 (SPI0 SCLK) | 23 |
| `SDA` | GPIO10 (SPI0 MOSI) | 19 |
| `RES` | GPIO17 | 11 |
| `DC` | GPIO25 | 22 |
| `CS` | GPIO8 (SPI0 CE0) | 24 |
| `BUSY` | GPIO24 | 18 |

`SCL` and `SDA` are the SPI clock and data inputs here, not the Pi's I²C pins. The display connections do not overlap the DAC's GPIO18, GPIO19 or GPIO21.

### Enable SPI and install the examples

Add the following to `/boot/firmware/config.txt`, keeping the existing DAC overlay intact:

```ini
dtparam=spi=on
```

Reboot, then check for `/dev/spidev0.0`:

```sh
ls -l /dev/spidev*
```

The display was tested with Raspbian 13 (Trixie). On that system, install the dependencies and download the Waveshare examples:

```sh
sudo apt update
sudo apt install git python3-pil python3-numpy python3-spidev python3-gpiozero python3-rpi.gpio python3-lgpio
git clone https://github.com/waveshare/e-Paper.git ~/e-Paper
```

Skip the clone if `~/e-Paper` already exists. Package availability and GPIO setup may differ on other DietPi or Raspberry Pi OS versions.

### Avoid the DAC pin conflict

The Waveshare Raspberry Pi configuration claims a `PWR_PIN` even though this display is powered directly from 3.3 V. Its default GPIO18 conflicts with the DAC's `BCK`. In `~/e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epdconfig.py`, change `PWR_PIN` **under `class RaspberryPi` only** from `18` to `22`:

```python
PWR_PIN = 22
```

Leave GPIO22 (physical pin 15) unused while running the display code. It is a software-only workaround and is not connected to the display. GPIO22 is distinct from physical pin 22, which is GPIO25 and connects to display `DC`. Do not change the `PWR_PIN` entries for the other platform classes.

### Display a test image

From the examples directory, run the V3 driver's normal full-refresh path. It displays one image and does not run the fast, partial or grayscale demos:

```sh
cd ~/e-Paper/RaspberryPi_JetsonNano/python/examples
sudo timeout --signal=INT --kill-after=5s 60s \
  env GPIOZERO_PIN_FACTORY=lgpio python3 -u - <<'PY'
import logging
import sys
sys.path.insert(0, '../lib')

from PIL import Image, ImageDraw
from waveshare_epd import epd2in9_V3, epdconfig

logging.basicConfig(level=logging.DEBUG)
display = epd2in9_V3.EPD()

try:
	print('Initializing V3...')
	if display.init() != 0:
		raise RuntimeError('Display initialization failed')

	image = Image.new('1', (display.height, display.width), 255)
	draw = ImageDraw.Draw(image)
	draw.text((20, 20), 'Hello from Pi!', fill=0)
	draw.rectangle((10, 10, 285, 115), outline=0, width=2)

	print('Refreshing...')
	display.display_Base(display.getbuffer(image))
	print('Refresh complete. Sending display to sleep...')
	display.sleep()
finally:
	epdconfig.module_exit(cleanup=True)
PY
```

The display may flash while refreshing. E-ink retains its image after power is removed. The timeout interrupts the test if it hangs; the Waveshare driver's BUSY wait otherwise has no timeout.

### Display troubleshooting

| Symptom | Cause / finding |
|---|---|
| `No access to /dev/mem` or GPIO backend errors | We ran the test as root with `GPIOZERO_PIN_FACTORY=lgpio` and installed `python3-lgpio` |
| `Failed to add edge detection` on BUSY | The `lgpio` GPIO Zero backend resolved this on our Trixie system |
| `lgpio.error: 'unexpected error'` while claiming GPIO18 | The display library's default `PWR_PIN` conflicted with the DAC. Use GPIO22 as above |
| Speckled screen or stuck at `e-Paper busy` | A misplaced GPIO wire caused this in our setup. Power off and check all connections, especially BUSY, before trying another driver. A driver mismatch or power problem can also cause trouble |

The full Waveshare example `epd_2in9_V3_test.py` also exercises fast, partial and grayscale updates. Those modes and other panel variants are not verified for this display.
