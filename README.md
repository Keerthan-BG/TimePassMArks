# Industrial Motor Digital Twin — Command-Injection Defense

A cyber-physical-systems (CPS) security demo built around a **real
electromagnetic model** of a 3-phase induction motor, not an animation. The
motor's speed, torque, current, slip, and winding temperature all come out
of the same equivalent-circuit and Newton's-law equations a controls
engineer would use to size a real drive. That physics model is then used
twice:

1. As the **plant** — the thing being controlled and attacked.
2. As a **digital twin** — a second, trusted copy that only ever sees
   authenticated commands, and whose predictions are compared against the
   real machine's reported state to catch attacks that bypass the perimeter
   defenses entirely.

## Why this design

Most "digital twin" student projects are a 3D model that spins when a
slider moves. This one treats the twin as a **security control**: a
model-based intrusion detector, which is how digital twins are actually
used in CPS/ICS security research. The specific threat modelled is
**command injection** — an attacker writing malicious speed/torque
setpoints to the drive — because that's the realistic, high-consequence
attack class against industrial motor drives (see: Stuxnet's centrifuge
speed manipulation, and the general literature on PLC/VFD command
injection).

## Architecture

```
backend/
  motor_model.py       Induction motor equivalent-circuit physics engine
  digital_twin.py       Shadow physics model + residual-based anomaly detector
  security.py            HMAC authentication + physics-based ramp-rate filter
  attack_simulator.py     Crafts the 4 attack payloads used by the dashboard
  app.py                  FastAPI: sim loop, REST API, WebSocket telemetry
frontend/
  index.html / style.css / app.js   Live HMI-style dashboard (no frameworks)
```

### The physics (`motor_model.py`)

Standard IEEE per-phase induction-motor equivalent circuit, solved every
0.5 ms sub-step via the Thevenin-equivalent method:

- Electromagnetic torque as a function of slip, stator/rotor resistance
  and leakage reactance, and magnetizing reactance.
- Scalar V/f control (constant volts-per-hertz), exactly how a real VFD
  drives the motor across its speed range.
- Mechanical dynamics: `J·dω/dt = T_e − T_load − Bω`, with a quadratic
  (pump/fan-type) load.
- A lumped single-mass thermal model, with an insulation-class
  overtemperature trip.

Sub-stepping matters here: the torque-slip curve is very steep near
synchronous speed, which makes the ODE numerically stiff. A single
20 Hz Euler step overshoots and diverges; sub-stepping at 0.5 ms keeps it
stable and physically correct (verified: full-load slip settles at ~3.4%,
locked-rotor current at ~4.6× running current — both realistic for a NEMA
Design B motor).

### The security model (`security.py`, `digital_twin.py`)

Two independent, individually-toggleable defensive layers, plus one that
can't be toggled off because it isn't in the command path at all:

| Layer | Catches | Toggle |
|---|---|---|
| HMAC authentication + anti-replay | Attackers without valid credentials; replayed captured traffic | `Auth` switch |
| Physics-based ramp-rate filter | Attackers *with* valid credentials sending physically implausible setpoint jumps | `Physics filter` switch |
| Digital twin residual | Anything that bypasses the command channel entirely (e.g. compromised VFD firmware writing the output register directly) | always on — this is the point |

The twin runs its own copy of the motor model, driven only by
authenticated, ramp-limited targets. It never sees the real field
frequency. If the real motor's reported speed drifts from what the twin
predicts a legitimately-commanded motor would be doing, that's the
residual alarm — a purely behavioural detector that needs no attack
signatures.

### Attack console

| Attack | What it is | What stops it |
|---|---|---|
| Unsigned Injection | No credentials at all | HMAC check |
| Replay Capture | Resends a captured, validly-signed command | Nonce/timestamp anti-replay |
| Stolen-Key Jump | Valid signature, but demands an instant 0→60Hz jump | Ramp-rate physics filter |
| Firmware Bypass | Writes the VFD's output register directly, never touching the authenticated channel | **Nothing at the perimeter** — only the digital twin's residual catches it |

Toggle the Auth / Physics-filter switches off and re-run the first three
attacks to see what an under-protected system looks like — the point of
the demo is comparing defended vs. undefended, not just "security good".

## Running it

```bash
cd digital-twin-project
pip install -r requirements.txt
cd backend
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`. The dashboard connects over WebSocket and
streams telemetry at 20 Hz.

## Suggested report/demo narrative

1. Send a normal signed command (e.g. 50 Hz) — watch the motor ramp up
   realistically and settle with ~3% slip, exactly like a real induction
   motor under load.
2. Fire "Unsigned Injection" and "Replay Capture" with defenses on — both
   rejected, logged with the specific reason.
3. Fire "Stolen-Key Jump" — rejected by the *physics* filter even though
   the signature is perfectly valid, illustrating why crypto-only ICS
   security is insufficient.
4. Turn the physics filter off and repeat the Stolen-Key Jump — now
   accepted, motor jumps unsafely, current spikes.
5. Fire "Firmware Bypass" with both defenses on — it still succeeds
   (nothing in the command channel can see it), but the residual scope
   spikes red immediately: the digital twin is the last line of defense
   when the perimeter is fully compromised.

## Possible extensions

- Multiple motors / a small plant topology with cross-correlated residuals
- Sensor-spoofing attacks (falsified telemetry rather than falsified
  commands) — the current architecture already separates "reported" from
  "twin-predicted" state, so this is a natural next attack class
- Persist event logs to a file/DB for a post-incident forensics view
- Swap the fixed HMAC secret for a rotating key exchange to demonstrate
  key-compromise recovery
  this is for git hub
