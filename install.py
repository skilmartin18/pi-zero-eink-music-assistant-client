#!/usr/bin/env python3
"""Set up the music player on a fresh DietPi installation."""

import getpass
import os
from pathlib import Path
import pwd
import shutil
import subprocess
from urllib.parse import urlsplit


PROJECT_DIR = Path(__file__).resolve().parent
BOOT_CONFIG = Path("/boot/firmware/config.txt")
HOSTS = Path("/etc/hosts")


def run(*command):
    subprocess.run(command, check=True)


def main():
    service_user = os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
    old_hostname = Path("/etc/hostname").read_text().strip()
    hostname = input(f"Hostname [{old_hostname}]: ").strip() or old_hostname
    url = input("Music Assistant URL e.g. (http://192.168.1.1:8095): ").strip()
    token = getpass.getpass("Music Assistant token: ")
    player = input(f"Music Assistant player name [{hostname}]: ").strip() or hostname
    wifi = input("Wi-Fi interface [wlan0]: ").strip() or "wlan0"

    while True:
        audio_client = input("Audio client: snapcast, sendspin, or none [snapcast]: ").strip().lower() or "snapcast"
        if audio_client in ("snapcast", "sendspin", "none"):
            break
        print("Choose snapcast, sendspin, or none.")
    snapclient = audio_client == "snapcast"
    if snapclient:
        default_server = urlsplit(url).hostname or ""
        snap_server = input(f"Snapserver hostname or IP [{default_server}]: ").strip() or default_server
        snap_port = input("Snapserver stream port [1704]: ").strip() or "1704"

    print("\nThis will configure the DAC, SPI, hostname, application, and startup service.")
    if input("Continue? [Y/n]: ").strip().lower() in ("n", "no"):
        print("Cancelled.")
        return

    run("apt", "update")
    run("apt", "install", "-y", "python3-venv", "python3-dev", "build-essential", "libjpeg-dev", "zlib1g-dev", "fonts-noto-cjk")
    if snapclient:
        run("apt", "install", "-y", "snapclient")

    venv = PROJECT_DIR / ".venv"
    run("sudo", "-u", service_user, "python3", "-m", "venv", "--system-site-packages", str(venv))
    run("sudo", "-u", service_user, str(venv / "bin/python"), "-m", "pip", "install", "-r", str(PROJECT_DIR / "requirements.txt"))

    env_path = PROJECT_DIR / ".env"
    env_path.touch(mode=0o600, exist_ok=True)
    with env_path.open("w") as env_file:
        for key, value in (("MA_URL", url), ("MA_TOKEN", token), ("MA_PLAYER", player), ("WIFI_INTERFACE", wifi)):
            value = value.replace("\\", "\\\\").replace("'", "\\'")
            env_file.write(f"{key}='{value}'\n")
            

    shutil.copy2(BOOT_CONFIG, str(BOOT_CONFIG) + ".bak")
    with BOOT_CONFIG.open("a") as boot_file:
        boot_file.write("\n[all]\ndtparam=audio=off\ndtparam=spi=on\ndtoverlay=hifiberry-dac\n")

    shutil.copy2(HOSTS, str(HOSTS) + ".bak")
    hosts_text = HOSTS.read_text()
    HOSTS.write_text(hosts_text.replace(old_hostname, hostname))
    Path("/etc/hostname").write_text(hostname + "\n")

    if snapclient:
        Path("/etc/default/snapclient").write_text(
            f'SNAPCLIENT_OPTS="--host {snap_server} --port {snap_port} --soundcard plughw:CARD=sndrpihifiberry,DEV=0"\n'
        )
        run("systemctl", "enable", "snapclient.service")

    if service_user != "root":
        run("usermod", "-a", "-G", "spi,gpio", service_user)

    directory = str(PROJECT_DIR).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    executable_dir = directory.replace("$", "$$")
    Path("/etc/systemd/system/pi-eink-music-player.service").write_text(f'''[Unit]
Description=pi music player eink monitor
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory="{directory}"
ExecStart="{executable_dir}/.venv/bin/python" -u "{executable_dir}/main.py"
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
''')
    
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "pi-eink-music-player.service")
    print("\nDone. Reboot to apply the hostname, DAC, and SPI settings.")
    if audio_client == "sendspin":
        print("\nAfter reboot, install Sendspin once the DAC is available:")
        print("  curl -fsSL https://raw.githubusercontent.com/Sendspin/sendspin-cpp-cli/main/scripts/get_started_linux.sh | bash")
        print("\nAfter installation, begin pairing on your Sendspin server. Monitor the player logs with:")
        print("  sudo journalctl -u sendspin-cli -f")
        print("Look for: I cli: *** Pairing code: 714536 *** -- type it into the server to pair.")
        print("The code shown above is an example; use the code from your logs.")


if __name__ == "__main__":
    main()