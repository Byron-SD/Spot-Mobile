# Spot Mobile

A phone web page for driving a Boston Dynamics Spot.

```
 Phone (browser)  ──Wi-Fi──▶  Laptop running server.py  ──gRPC (bosdyn SDK)──▶  Spot
```

A browser can't talk to Spot directly, because Spot's API is authenticated gRPC.
`server.py` is a small bridge. It logs in to Spot with the official SDK and holds
the lease and a software E-Stop. It also serves the control page. Your phone just
opens the page.

## Setup

Tested with Python 3.14 and `bosdyn-client` 5.2.0. A `.venv` with these packages
already exists in this folder. To create a fresh one:

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## Try it without a robot

```bash
python server.py --mock
```

Open http://localhost:8000. On your phone, open `http://<laptop-ip>:8000` while on the same Wi-Fi.

## Drive Spot

1. Connect the laptop to Spot's Wi-Fi (Spot is usually `192.168.80.3`), or put both
   on the same network.
2. Set credentials and a PIN for the page:
   ```powershell
   $env:BOSDYN_CLIENT_USERNAME = "user"
   $env:BOSDYN_CLIENT_PASSWORD = "password"
   $env:SPOT_UI_PIN = "4321"
   ```
3. Start the bridge:
   ```bash
   python server.py --hostname 192.168.80.3
   ```
4. Connect your phone to the same network and open `http://<laptop-ip>:8000`.
   Windows asks whether to allow Python through the firewall. Allow it on private networks.
   Find the laptop IP with `ipconfig`.
5. For a full-screen app, use "Add to Home Screen".

The bridge leaves Spot alone until a phone connects. Then it **takes the lease** from
any other client, including the tablet, and replaces Spot's E-Stop configuration with
its own software E-Stop. To keep the tablet or a hardware E-Stop in charge, start the
bridge with `--no-estop`. The phone's E-STOP button is then disabled.

## Drive Spot from your laptop

The same bridge serves a laptop page at `/laptop`. It has keyboard and gamepad driving
and a live camera view.

1. Connect the laptop to Spot's Wi-Fi (or the same network as Spot).
2. Run the launcher. It asks for Spot's username and password, starts the bridge on
   this laptop only (no PIN needed), and opens the page:
   ```powershell
   .\start-laptop.ps1                      # Spot at 192.168.80.3
   .\start-laptop.ps1 -Hostname 10.0.0.42  # Spot on another network
   .\start-laptop.ps1 -Mock                # try it without a robot
   ```
   If PowerShell blocks the script, run it once with
   `powershell -ExecutionPolicy Bypass -File .\start-laptop.ps1`.
3. Press **P** to power on, then **F** to stand, then hold **W A S D / Q E** to walk.

| Key | Action |
|---|---|
| `W` `S` / `↑` `↓` | forward / back |
| `A` `D` | strafe left / right |
| `Q` `E` / `←` `→` | turn left / right |
| `Shift` (held) | half speed |
| `Space` | **E-STOP** (release with the on-screen button) |
| `X` | stop motion, motors stay on |
| `P` | power on / off |
| `F` / `V` | stand / sit |
| `[` `]` | speed down / up |
| `-` `=` | body height down / up |

An Xbox-style gamepad also works: left stick moves and strafes, right stick turns, and B stops.

The camera dropdown switches between Spot's five body cameras. The front cameras are
rotated so they show upright.

Spot moves only while a key or stick is held. If the browser window loses focus, the
page sends Stop.

## Run it on Spot's CORE I/O (no laptop)

If your Spot has a CORE I/O payload, the bridge can run on the robot as a Spot
Extension. Your phone then only needs Spot's Wi-Fi.

To check whether your Spot has a CORE I/O, open `https://192.168.80.3:21443` on Spot's
Wi-Fi. A login page means you have one.

**Build the extension (once per change):**

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
   It builds the ARM64 image the CORE I/O needs.
2. Copy `extension.env.example` to `extension.env` and fill it in. Use a dedicated Spot
   user account, because the credentials go into the package.
3. Build:
   ```bash
   .venv\Scripts\python build_extension.py
   ```
   This writes `dist/spot_mobile.spx`. The first build takes several minutes because
   it emulates ARM64.

**Install it:**

1. Connect the laptop to Spot's Wi-Fi and open `https://192.168.80.3:21443`.
2. Log in, go to **Extensions**, and upload `dist/spot_mobile.spx`.
3. The extension starts now and on every boot. It waits for Spot to come up.

**Use it:** connect your phone to Spot's Wi-Fi and open `http://192.168.80.3:21080`,
or the port you set in `extension.env`. Add it to your home screen for a full-screen icon.

Extension logs are in the same portal, under the extension's entry.

Notes:
- Spot forwards only ports 21000–22000 to the CORE I/O. Port 21443 is the portal's.
- Inside the CORE I/O, the bridge reaches Spot at `192.168.50.3`. The build script sets this.
- The `.spx` contains your Spot credentials. Don't share it or commit it.

## Controls

| Control | Walk mode | Body pose mode |
|---|---|---|
| Left stick | forward/back + strafe | roll |
| Right stick | turn | yaw + pitch (look around) |
| Height slider | walking body height | standing body height |
| Speed slider | scales all speeds | – |

- **E-STOP** reacts on touch-down. It runs "settle then cut": Spot sits, then motors cut.
  Tap it again to release the E-Stop.
- **Stop** stops the current motion but keeps the motors on.
- **Roll over** puts Spot in the battery-change pose. **Self-right** recovers Spot after a fall.

## Safety behaviour

- Spot moves only while a thumb is on a stick. Each velocity command expires
  after 0.6 s, so Spot stops by itself if the phone, Wi-Fi or page drops.
- If the page is hidden (screen locked or app switched), the page sends Stop.
- If the phone disconnects, the bridge sends Stop. It keeps control, so a brief Wi-Fi
  drop doesn't hand Spot back to the tablet mid-task. The tablet can take control back
  at any time. To take it back to the phone, tap **Reconnect**.
- If the bridge process dies, the software E-Stop times out after 9 s and Spot sits and cuts power.
- The server clamps speeds to 1.0 m/s forward, 0.5 m/s strafe and 1.0 rad/s turn,
  whatever the page sends.
- Only one phone can control Spot at a time.

The phone E-Stop is a software E-Stop over Wi-Fi. It does **not** replace a
hardware or tablet E-Stop. Keep a hardware or tablet E-Stop within reach when you drive.
