"""
app.py
------
Wires the motor physics engine, the digital twin, and the security layer
together into a live real-time simulation, exposed over a small REST API
and a WebSocket telemetry feed. Serves the dashboard frontend too, so the
whole project runs from one command:

    pip install -r requirements.txt
    uvicorn app:app --reload

then open http://127.0.0.1:8000
"""

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from motor_model import InductionMotor, MotorParameters
from digital_twin import DigitalTwin
from security import SecurityMonitor, SecurityEvent, MAX_RAMP_HZ_PER_S, MAX_FREQ_HZ
import attack_simulator as atk

DT = 0.05                    # 20 Hz simulation / telemetry rate
FIRMWARE_OVERRIDE_DURATION_S = 4.0

app = FastAPI(title="Industrial Motor Digital Twin")


class SimState:
    def __init__(self):
        self.motor = InductionMotor(MotorParameters())
        self.twin = DigitalTwin(MotorParameters(), max_ramp_hz_s=MAX_RAMP_HZ_PER_S)
        self.security = SecurityMonitor()

        self.authenticated_target_hz = 0.0   # last target that passed security
        self.field_freq_hz = 0.0             # what the field VFD is actually outputting

        self.override_active = False
        self.override_freq_hz = 0.0
        self.override_until = 0.0

        self.last_legit_command = None
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    def reset(self):
        self.__init__()


state = SimState()


# ---------------------------------------------------------------- schemas --
class CommandIn(BaseModel):
    frequency_hz: float


class AttackIn(BaseModel):
    type: str                 # unsigned | replay | stolen_key_jump | firmware_bypass
    frequency_hz: float = 60.0


class SecurityConfigIn(BaseModel):
    auth_enabled: bool | None = None
    physics_filter_enabled: bool | None = None


# ------------------------------------------------------------- simulation --
async def simulation_loop():
    while True:
        async with state.lock:
            now = time.time()

            if state.override_active:
                if now >= state.override_until:
                    state.override_active = False
                else:
                    state.field_freq_hz = state.override_freq_hz

            if not state.override_active:
                max_delta = MAX_RAMP_HZ_PER_S * DT
                delta = state.authenticated_target_hz - state.field_freq_hz
                delta = max(-max_delta, min(max_delta, delta))
                state.field_freq_hz += delta

            real_snap = state.motor.step(DT, state.field_freq_hz)
            twin_snap = state.twin.step(
                DT, state.authenticated_target_hz, real_snap, state.motor.time_s
            )
            sec_snap = state.security.snapshot()

            payload = {
                "motor": real_snap,
                "twin": twin_snap,
                "security": sec_snap,
                "field": {
                    "authenticated_target_hz": round(state.authenticated_target_hz, 2),
                    "field_freq_hz": round(state.field_freq_hz, 2),
                    "override_active": state.override_active,
                },
            }
            dead = set()
            for ws in state.clients:
                try:
                    await ws.send_text(json.dumps(payload))
                except Exception:
                    dead.add(ws)
            state.clients -= dead

        await asyncio.sleep(DT)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(simulation_loop())


# ------------------------------------------------------------------- API --
@app.post("/api/command")
async def send_command(cmd_in: CommandIn):
    async with state.lock:
        freq = max(0.0, min(MAX_FREQ_HZ, cmd_in.frequency_hz))
        cmd = atk.make_legit_command(freq, label=f"operator setpoint -> {freq:.1f}Hz")
        ok, evt = state.security.evaluate(cmd, state.motor.time_s)
        if ok:
            state.authenticated_target_hz = freq
            state.last_legit_command = cmd
        return {"accepted": ok, "verdict": evt.verdict, "detail": evt.detail}


@app.post("/api/attack")
async def send_attack(atk_in: AttackIn):
    async with state.lock:
        freq = max(0.0, min(MAX_FREQ_HZ, atk_in.frequency_hz))

        if atk_in.type == "unsigned":
            cmd = atk.make_unsigned_injection(freq)
        elif atk_in.type == "replay":
            if state.last_legit_command is None:
                return {"accepted": False, "verdict": "NO_CAPTURE",
                         "detail": "no legitimate command has been sent yet to replay"}
            cmd = atk.make_replay_attack(state.last_legit_command)
        elif atk_in.type == "stolen_key_jump":
            cmd = atk.make_stolen_key_jump(freq)
        elif atk_in.type == "firmware_bypass":
            state.override_active = True
            state.override_freq_hz = freq
            state.override_until = time.time() + FIRMWARE_OVERRIDE_DURATION_S
            evt_detail = (
                "direct write to VFD output register -- never touched the "
                "authenticated command channel, so auth/physics filters cannot "
                "see it. Only the digital twin's behavioural residual can."
            )
            evt = SecurityEvent(state.motor.time_s, "attacker",
                                 f"firmware-level override -> {freq:.0f}Hz",
                                 "OFF_CHANNEL", evt_detail)
            state.security.events.append(evt)
            return {"accepted": True, "verdict": "OFF_CHANNEL", "detail": evt_detail}
        else:
            return {"accepted": False, "verdict": "UNKNOWN_TYPE", "detail": "unknown attack type"}

        ok, evt = state.security.evaluate(cmd, state.motor.time_s)
        if ok:
            state.authenticated_target_hz = freq
        return {"accepted": ok, "verdict": evt.verdict, "detail": evt.detail}


@app.post("/api/security/config")
async def configure_security(cfg: SecurityConfigIn):
    async with state.lock:
        if cfg.auth_enabled is not None:
            state.security.auth_enabled = cfg.auth_enabled
        if cfg.physics_filter_enabled is not None:
            state.security.physics_filter_enabled = cfg.physics_filter_enabled
        return {
            "auth_enabled": state.security.auth_enabled,
            "physics_filter_enabled": state.security.physics_filter_enabled,
        }


@app.post("/api/reset")
async def reset_sim():
    async with state.lock:
        state.reset()
    return {"ok": True}


@app.get("/api/state")
async def get_state():
    async with state.lock:
        return {
            "motor": state.motor.snapshot(),
            "security": state.security.snapshot(),
            "field": {
                "authenticated_target_hz": state.authenticated_target_hz,
                "field_freq_hz": state.field_freq_hz,
                "override_active": state.override_active,
            },
        }


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    await ws.accept()
    state.clients.add(ws)
    try:
        while True:
            await ws.receive_text()   # we don't expect client->server messages, just keep alive
    except WebSocketDisconnect:
        state.clients.discard(ws)


# --------------------------------------------------------------- frontend --
# Mounted LAST and at "/" so the API/WebSocket routes above (registered
# first) still take priority; this just catches everything else, including
# "/" itself (html=True serves index.html for it) and the relative
# style.css / app.js references inside index.html.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
