"""
digital_twin.py
----------------
The actual "digital twin" in the CPS-security sense: a second, independent
instance of the motor physics model that only ever sees AUTHENTICATED,
ramp-limited targets -- never the raw drive output. It continuously predicts
what the real motor's speed/current *should* be if only legitimate commands
had ever reached the field device.

The gap between the twin's prediction and what the field device actually
reports is the "residual". A big, sustained residual means one of two things:
  (a) an attacker pushed a command straight to the drive, bypassing the
      authenticated control path (classic ICS command injection), or
  (b) the field device is lying about its own state (sensor spoofing /
      compromised telemetry).
Either way, the twin doesn't need to know *how* the attack happened -- it
just knows physics, and physics was violated. This is exactly the
"process-aware" / model-based intrusion detection approach used in real
industrial-control security research, as opposed to signature-based IDS
that only catches attacks it has seen before.
"""

from collections import deque

from motor_model import InductionMotor, MotorParameters


class DigitalTwin:
    def __init__(self, params: MotorParameters = None, max_ramp_hz_s: float = 5.0):
        self.shadow_motor = InductionMotor(params)
        self.expected_freq_hz = 0.0
        self.max_ramp_hz_s = max_ramp_hz_s

        self.residual_rpm_threshold = 60.0
        self.residual_freq_threshold = 3.0
        self.residual_history: deque = deque(maxlen=400)

        self.alarm = False
        self.alarm_since_s = None

    def step(self, dt: float, authenticated_target_hz: float, real_snapshot: dict, sim_time_s: float):
        # Ramp the twin's belief toward the last AUTHENTICATED target only,
        # exactly like a correctly configured VFD would ramp toward its setpoint.
        max_delta = self.max_ramp_hz_s * dt
        delta = authenticated_target_hz - self.expected_freq_hz
        delta = max(-max_delta, min(max_delta, delta))
        self.expected_freq_hz += delta

        shadow_snap = self.shadow_motor.step(dt, self.expected_freq_hz)

        residual_rpm = abs(real_snapshot["rpm"] - shadow_snap["rpm"])
        residual_freq_hz = abs(real_snapshot["applied_freq_hz"] - self.expected_freq_hz)
        self.residual_history.append(residual_rpm)

        triggered = (
            residual_rpm > self.residual_rpm_threshold
            or residual_freq_hz > self.residual_freq_threshold
        )
        if triggered and not self.alarm:
            self.alarm = True
            self.alarm_since_s = sim_time_s
        elif not triggered and self.alarm and residual_rpm < self.residual_rpm_threshold * 0.4:
            self.alarm = False
            self.alarm_since_s = None

        return {
            "expected_freq_hz": round(self.expected_freq_hz, 3),
            "expected_rpm": shadow_snap["rpm"],
            "residual_rpm": round(residual_rpm, 1),
            "residual_freq_hz": round(residual_freq_hz, 2),
            "twin_alarm": self.alarm,
            "alarm_since_s": self.alarm_since_s,
        }
