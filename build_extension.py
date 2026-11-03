"""Build Spot Mobile as a Spot Extension (.spx) for the CORE I/O.

    python build_extension.py

Needs Docker Desktop (it builds the linux/arm64 image through emulation) and an
extension.env file (copy extension.env.example). Writes dist/spot_mobile.spx, which
you upload in the CORE I/O web portal under Extensions.
"""

import gzip
import json
import shutil
import struct
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path

NAME = "spot_mobile"
VERSION = "1.0.0"
IMAGE = f"{NAME}:{VERSION}"
IMAGE_FILE = f"{NAME}_image.tgz"

# Spot's own address as seen from the CORE I/O.
ROBOT_IP_FROM_COREIO = "192.168.50.3"

ROOT = Path(__file__).parent
BUILD = ROOT / "build" / NAME
DIST = ROOT / "dist"

COMPOSE = f"""services:
  {NAME}:
    image: {IMAGE}
    network_mode: host
    restart: unless-stopped
    mem_limit: 512m
    env_file: {NAME}.env
    environment:
      - SPOT_HOSTNAME={ROBOT_IP_FROM_COREIO}
"""


def fail(msg):
    sys.exit(f"error: {msg}")


def read_env():
    path = ROOT / "extension.env"
    if not path.exists():
        fail("extension.env not found. Copy extension.env.example to extension.env and fill it in.")
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    for key in ("BOSDYN_CLIENT_USERNAME", "BOSDYN_CLIENT_PASSWORD"):
        if not env.get(key):
            fail(f"{key} is empty in extension.env")
    port = int(env.get("SPOT_UI_PORT") or 21080)
    if not 21000 <= port <= 22000 or port == 21443:
        fail("SPOT_UI_PORT must be 21000-22000 and not 21443 (reserved by the CORE I/O portal)")
    env["SPOT_UI_PORT"] = str(port)
    if not env.get("SPOT_UI_PIN"):
        print("warning: SPOT_UI_PIN is empty; anyone on Spot's Wi-Fi could drive the robot.")
    return env


def run(*cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def build_image(out_path):
    if shutil.which("docker") is None:
        fail("docker not found. Install Docker Desktop and make sure it is running.")
    run("docker", "buildx", "build", "--platform", "linux/arm64", "-t", IMAGE, "--load", str(ROOT))
    print(f"+ docker save {IMAGE} | gzip > {out_path.name}")
    with subprocess.Popen(["docker", "save", IMAGE], stdout=subprocess.PIPE) as proc, \
            gzip.open(out_path, "wb") as out:
        shutil.copyfileobj(proc.stdout, out)
    if proc.returncode != 0:
        fail("docker save failed")


def write_icon(path, size=64):
    """A plain Spot-yellow square so the extension has an icon in the portal."""
    row = b"\x00" + bytes((245, 196, 0)) * size
    raw = row * size

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main():
    env = read_env()

    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    DIST.mkdir(exist_ok=True)

    build_image(BUILD / IMAGE_FILE)

    (BUILD / "manifest.json").write_text(json.dumps({
        "extension_name": NAME,
        "description": "Drive Spot from a phone browser",
        "version": VERSION,
        "images": [IMAGE_FILE],
        "icon": "icon.png",
    }, indent=2))
    (BUILD / "docker-compose.yml").write_text(COMPOSE)
    (BUILD / f"{NAME}.env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    write_icon(BUILD / "icon.png")

    spx = DIST / f"{NAME}.spx"
    with tarfile.open(spx, "w:gz") as tar:
        for f in sorted(BUILD.iterdir()):
            tar.add(f, arcname=f.name)

    print(f"\nBuilt {spx} ({spx.stat().st_size / 1e6:.0f} MB)")
    print("It contains your Spot credentials. Don't share it.")
    print(f"After installing, open http://192.168.80.3:{env['SPOT_UI_PORT']} on Spot's Wi-Fi.")


if __name__ == "__main__":
    main()
