"""Spot Mobile - a phone-friendly web controller for Boston Dynamics Spot.

Runs a small web server (FastAPI) that talks to Spot through the official
bosdyn-client SDK. Your phone opens the page served here and sends commands
over a WebSocket.

    python server.py --mock                 # try the UI without a robot
    python server.py --hostname 192.168.80.3

Phone page: http://<laptop-ip>:8000    Laptop page (keyboard + camera): http://localhost:8000/laptop

Credentials come from BOSDYN_CLIENT_USERNAME / BOSDYN_CLIENT_PASSWORD.
Set SPOT_UI_PIN to require a PIN before the phone can drive.
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

log = logging.getLogger("spot-mobile")

STATIC_DIR = Path(__file__).parent / "static"

# Hard limits, applied server-side regardless of what the page sends.
MAX_VX = 1.0    # m/s forward/back
MAX_VY = 0.5    # m/s strafe
MAX_VROT = 1.0  # rad/s turn
VELOCITY_CMD_DURATION = 0.6  # s; robot stops on its own if no new command arrives

# Commands that need the lease. Stop and E-Stop deliberately don't, so they never
# wait on (or fail because of) taking control.
NEEDS_CONTROL = {"move", "stand", "power_on", "power_off", "sit", "self_right", "battery_change"}

# Spot's body cameras, and how far to rotate each image so it shows upright.
CAMERA_ROTATION = {
    "frontleft_fisheye_image": -78,
    "frontright_fisheye_image": -102,
    "left_fisheye_image": 0,
    "right_fisheye_image": 180,
    "back_fisheye_image": 0,
}


def clamp(value, limit):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(-limit, min(limit, value))


class SpotRobot:
    """Thin wrapper over the bosdyn SDK holding the lease and E-Stop.

    Connecting (login, time sync) and taking control (lease, E-Stop) are separate
    steps, so the server can start before Spot is ready and leaves the tablet in
    charge until a phone actually connects.
    """

    def __init__(self, hostname, use_estop=True):
        self.hostname = hostname
        self.use_estop = use_estop
        self.robot = None
        self.estop_keepalive = None
        self.lease_keepalive = None
        self.body_height = 0.0
        self.connect_error = "Connecting to Spot..."
        self._control_lock = threading.Lock()

    def connect(self):
        import bosdyn.client
        from bosdyn.client.image import ImageClient
        from bosdyn.client.robot_command import RobotCommandBuilder, RobotCommandClient
        from bosdyn.client.robot_state import RobotStateClient
        from bosdyn.geometry import EulerZXY

        self._builder = RobotCommandBuilder
        self._euler = EulerZXY

        username = os.environ.get("BOSDYN_CLIENT_USERNAME")
        password = os.environ.get("BOSDYN_CLIENT_PASSWORD")
        if not username or not password:
            raise RuntimeError("set BOSDYN_CLIENT_USERNAME and BOSDYN_CLIENT_PASSWORD")

        sdk = bosdyn.client.create_standard_sdk("SpotMobile")
        robot = sdk.create_robot(self.hostname)
        robot.authenticate(username, password)
        robot.time_sync.wait_for_sync()

        self.command_client = robot.ensure_client(RobotCommandClient.default_service_name)
        self.state_client = robot.ensure_client(RobotStateClient.default_service_name)
        self.image_client = robot.ensure_client(ImageClient.default_service_name)
        self.robot = robot
        self.connect_error = None

    def connect_forever(self, retry_secs=5):
        """Keep trying to connect; Spot may still be booting when this starts."""
        while self.robot is None:
            try:
                self.connect()
                log.info("Connected to Spot at %s", self.hostname)
            except Exception as exc:
                self.connect_error = f"Can't reach Spot: {exc}"
                log.warning("%s (retrying in %ss)", self.connect_error, retry_secs)
                time.sleep(retry_secs)

    def take_control(self, force=False):
        """Register the software E-Stop and take the lease. Safe to call repeatedly.

        force=True re-takes the lease even if we think we hold it, e.g. after the
        tablet took it; a phone (re)connecting does this.
        """
        from bosdyn.client.estop import EstopClient, EstopEndpoint, EstopKeepAlive
        from bosdyn.client.lease import LeaseClient, LeaseKeepAlive

        if self.robot is None:
            raise RuntimeError(self.connect_error)
        with self._control_lock:
            # Software E-Stop. While this keepalive runs, the phone's E-STOP button can cut
            # the motors. If this server dies, the E-Stop times out and Spot sits and cuts power.
            if self.use_estop and self.estop_keepalive is None:
                estop_client = self.robot.ensure_client(EstopClient.default_service_name)
                endpoint = EstopEndpoint(estop_client, "SpotMobile", estop_timeout=9.0)
                endpoint.force_simple_setup()
                self.estop_keepalive = EstopKeepAlive(endpoint)

            # Take control from any other client (e.g. the tablet).
            if force and self.lease_keepalive is not None:
                self.lease_keepalive.shutdown()
                self.lease_keepalive = None
            if self.lease_keepalive is None or not self.lease_keepalive.is_alive():
                lease_client = self.robot.ensure_client(LeaseClient.default_service_name)
                lease_client.take()
                self.lease_keepalive = LeaseKeepAlive(lease_client, must_acquire=True, return_at_exit=True)
                log.info("Took control of Spot")

    def _send(self, cmd, end_time_secs=None):
        self.command_client.robot_command(cmd, end_time_secs=end_time_secs)

    def power_on(self):
        self.robot.power_on(timeout_sec=20)

    def power_off(self):
        self.robot.power_off(cut_immediately=False, timeout_sec=20)

    def stand(self, height=0.0, yaw=0.0, roll=0.0, pitch=0.0):
        self.body_height = height
        orientation = self._euler(yaw=yaw, roll=roll, pitch=pitch)
        self._send(self._builder.synchro_stand_command(body_height=height, footprint_R_body=orientation))

    def sit(self):
        self._send(self._builder.synchro_sit_command())

    def move(self, vx, vy, vrot):
        cmd = self._builder.synchro_velocity_command(v_x=vx, v_y=vy, v_rot=vrot, body_height=self.body_height)
        self._send(cmd, end_time_secs=time.time() + VELOCITY_CMD_DURATION)

    def stop(self):
        self._send(self._builder.stop_command())

    def self_right(self):
        self._send(self._builder.selfright_command())

    def battery_change_pose(self):
        self._send(self._builder.battery_change_pose_command())

    def estop(self):
        if self.estop_keepalive:
            self.estop_keepalive.settle_then_cut()

    def estop_release(self):
        if self.estop_keepalive:
            self.estop_keepalive.allow()

    def camera(self, source):
        """Return (jpeg bytes, mime type) for one camera. Doesn't need the lease."""
        from bosdyn.api import image_pb2
        from bosdyn.client.image import build_image_request

        if self.robot is None:
            raise RuntimeError(self.connect_error)
        request = build_image_request(source, quality_percent=60, image_format=image_pb2.Image.FORMAT_JPEG)
        response = self.image_client.get_image([request])[0]
        return response.shot.image.data, "image/jpeg"

    def status(self):
        if self.robot is None:
            return {"connected": False, "error": self.connect_error}
        state = self.state_client.get_robot_state()
        battery = None
        if state.battery_states:
            battery = round(state.battery_states[0].charge_percentage.value)
        return {
            "connected": True,
            "powered": self.robot.is_powered_on(),
            "estopped": self.robot.is_estopped(),
            "battery": battery,
            "software_estop": self.estop_keepalive is not None,
        }

    def shutdown(self):
        try:
            if self.lease_keepalive:
                self.lease_keepalive.shutdown()
        finally:
            if self.estop_keepalive:
                self.estop_keepalive.shutdown()


class MockRobot:
    """Stands in for Spot so the UI can be tried without a robot."""

    def __init__(self):
        self.powered = False
        self.estopped = False
        self.battery = 87

    def take_control(self, force=False):
        pass

    def power_on(self):
        if self.estopped:
            raise RuntimeError("Robot is E-Stopped. Release the E-Stop first.")
        time.sleep(1)
        self.powered = True

    def power_off(self):
        time.sleep(1)
        self.powered = False

    def _require_power(self):
        if not self.powered:
            raise RuntimeError("Motors are off. Power on first.")

    def stand(self, height=0.0, yaw=0.0, roll=0.0, pitch=0.0):
        self._require_power()
        log.info("[mock] stand h=%.2f yaw=%.2f roll=%.2f pitch=%.2f", height, yaw, roll, pitch)

    def sit(self):
        self._require_power()
        log.info("[mock] sit")

    def move(self, vx, vy, vrot):
        self._require_power()
        log.debug("[mock] move vx=%.2f vy=%.2f vrot=%.2f", vx, vy, vrot)

    def stop(self):
        log.info("[mock] stop")

    def self_right(self):
        self._require_power()
        log.info("[mock] self-right")

    def battery_change_pose(self):
        self._require_power()
        log.info("[mock] battery change pose")

    def estop(self):
        self.estopped = True
        self.powered = False

    def estop_release(self):
        self.estopped = False

    def camera(self, source):
        time.sleep(0.1)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480">'
               f'<rect width="640" height="480" fill="#222"/>'
               f'<text x="320" y="230" fill="#888" font-size="28" font-family="sans-serif" text-anchor="middle">'
               f'MOCK {source}</text>'
               f'<text x="320" y="280" fill="#f5c400" font-size="40" font-family="monospace" text-anchor="middle">'
               f'{time.strftime("%H:%M:%S")}</text></svg>')
        return svg.encode(), "image/svg+xml"

    def status(self):
        return {
            "connected": True,
            "powered": self.powered,
            "estopped": self.estopped,
            "battery": self.battery,
            "software_estop": True,
            "mock": True,
        }

    def shutdown(self):
        pass


def create_app(robot, pin=None):
    app = FastAPI()
    # One phone drives at a time; a second connection is refused.
    active = {"ws": None}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/laptop")
    def laptop():
        return FileResponse(STATIC_DIR / "laptop.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    async def run(fn, *args):
        return await asyncio.to_thread(fn, *args)

    @app.websocket("/ws")
    async def control(ws: WebSocket):
        await ws.accept()

        if pin is not None:
            try:
                hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
            except Exception:
                await ws.close(code=4001)
                return
            if hello.get("type") != "auth" or str(hello.get("pin")) != pin:
                await ws.send_json({"type": "error", "message": "Wrong PIN"})
                await ws.close(code=4001)
                return

        if active["ws"] is not None:
            await ws.send_json({"type": "error", "message": "Another device is already controlling Spot"})
            await ws.close(code=4002)
            return
        active["ws"] = ws

        await ws.send_json({"type": "ready"})
        log.info("Controller connected: %s", ws.client)
        try:
            await run(robot.take_control, True)
        except Exception as exc:
            log.warning("Could not take control: %s", exc)
            await ws.send_json({"type": "error", "message": f"Could not take control: {exc}"})

        async def push_status():
            while True:
                try:
                    status = await run(robot.status)
                except Exception as exc:
                    status = {"connected": False, "error": str(exc)}
                await ws.send_json({"type": "status", **status})
                await asyncio.sleep(1.0)

        status_task = asyncio.create_task(push_status())
        camera = {"task": None}

        async def send_frame(source):
            try:
                data, mime = await run(robot.camera, source)
                frame = {"data": base64.b64encode(data).decode(), "mime": mime}
            except Exception as exc:
                frame = {"error": str(exc)}
            try:
                await ws.send_json({"type": "image", "source": source,
                                    "rotation": CAMERA_ROTATION[source], **frame})
            except Exception:
                pass  # page went away mid-frame

        actions = {
            "power_on": robot.power_on,
            "power_off": robot.power_off,
            "sit": robot.sit,
            "stop": robot.stop,
            "self_right": robot.self_right,
            "battery_change": robot.battery_change_pose,
            "estop": robot.estop,
            "estop_release": robot.estop_release,
        }

        try:
            while True:
                msg = json.loads(await ws.receive_text())
                kind = msg.get("type")
                try:
                    if kind in NEEDS_CONTROL:
                        # Re-takes control if the tablet grabbed it or Spot came up after the phone connected.
                        await run(robot.take_control)
                    if kind == "camera":
                        # One frame in flight at a time; the page asks for the next one when this arrives.
                        source = msg.get("source")
                        if source in CAMERA_ROTATION and (camera["task"] is None or camera["task"].done()):
                            camera["task"] = asyncio.create_task(send_frame(source))
                    elif kind == "move":
                        await run(robot.move,
                                  clamp(msg.get("vx"), MAX_VX),
                                  clamp(msg.get("vy"), MAX_VY),
                                  clamp(msg.get("vrot"), MAX_VROT))
                    elif kind == "stand":
                        await run(robot.stand,
                                  clamp(msg.get("height", 0), 0.15),
                                  clamp(msg.get("yaw", 0), 0.5),
                                  clamp(msg.get("roll", 0), 0.4),
                                  clamp(msg.get("pitch", 0), 0.5))
                    elif kind in actions:
                        log.info("Command: %s", kind)
                        await run(actions[kind])
                        await ws.send_json({"type": "ack", "command": kind})
                except WebSocketDisconnect:
                    raise  # page closed mid-reply; handled below
                except Exception as exc:
                    log.warning("Command %s failed: %s", kind, exc)
                    await ws.send_json({"type": "error", "message": f"{kind}: {exc}"})
        except WebSocketDisconnect:
            pass
        finally:
            status_task.cancel()
            if camera["task"]:
                camera["task"].cancel()
            active["ws"] = None
            log.info("Controller disconnected; stopping robot")
            try:
                await run(robot.stop)
            except Exception as exc:
                log.warning("Stop on disconnect failed: %s", exc)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hostname", default=os.environ.get("SPOT_HOSTNAME", "192.168.80.3"),
                        help="Spot's IP address (default 192.168.80.3, Spot's own Wi-Fi)")
    parser.add_argument("--mock", action="store_true", help="Run without a robot")
    parser.add_argument("--no-estop", action="store_true",
                        help="Don't register a software E-Stop (use when another E-Stop, e.g. the tablet, must stay in charge)")
    parser.add_argument("--host", default="0.0.0.0", help="Interface for the web server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SPOT_UI_PORT", 8000)))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.mock:
        robot = MockRobot()
    else:
        robot = SpotRobot(args.hostname, use_estop=not args.no_estop)
        threading.Thread(target=robot.connect_forever, daemon=True).start()
    pin = os.environ.get("SPOT_UI_PIN") or None
    if pin is None and args.host not in ("127.0.0.1", "localhost"):
        log.warning("SPOT_UI_PIN is not set: anyone on this network can open the page and drive Spot.")

    log.info("Laptop controls: http://localhost:%s/laptop", args.port)
    try:
        uvicorn.run(create_app(robot, pin), host=args.host, port=args.port)
    finally:
        robot.shutdown()


if __name__ == "__main__":
    main()
