"""
motor_model.py
--------------
A physics-based model of a 3-phase squirrel-cage induction motor.

This is NOT a lookup table or an animation curve. Every value (torque, current,
slip, temperature) is derived every timestep from the motor's per-phase
equivalent circuit and Newton's second law for rotation, driven by whatever
electrical frequency the drive is actually applying. If you feed it a bad
command, it responds the way a real motor would -- including badly.

Model used: single-cage induction motor equivalent circuit (IEEE recommended
form), solved via the Thevenin equivalent for the rotor branch. Reference
values (locked-rotor / breakdown torque behaviour, slip range, thermal time
constant) are typical of a 7.5 kW / 10 HP, 415 V, 4-pole industrial motor
driving a centrifugal pump/fan-type load -- a very common ICS/SCADA asset.
"""

import math
from dataclasses import dataclass


@dataclass
class MotorParameters:
    # --- Nameplate ---
    rated_power_w: float = 7500.0          # 10 HP
    rated_voltage_ln: float = 239.6        # line-neutral volts (415V line-line)
    rated_freq_hz: float = 50.0
    poles: int = 4

    # --- Per-phase equivalent circuit (ohms, referred to stator) ---
    r1: float = 0.52     # stator resistance
    x1: float = 1.20      # stator leakage reactance @ rated freq
    r2: float = 0.42     # rotor resistance (referred)
    x2: float = 1.20      # rotor leakage reactance (referred) @ rated freq
    xm: float = 28.0      # magnetizing reactance @ rated freq

    # --- Mechanical ---
    inertia_kgm2: float = 0.058     # rotor + coupled load inertia (J)
    friction_coeff: float = 0.0025  # viscous friction B (N*m*s/rad)

    # --- Thermal (lumped single-mass model) ---
    thermal_capacitance: float = 2600.0   # J/°C  (winding + core lumped mass)
    thermal_resistance: float = 0.62      # °C/W to ambient
    ambient_temp_c: float = 25.0
    warn_temp_c: float = 120.0
    trip_temp_c: float = 155.0            # Class F insulation limit

    # --- Load (typical centrifugal pump/fan: torque grows with speed^2) ---
    load_k_quadratic: float = 0.0031   # N*m per (rad/s)^2
    load_static_nm: float = 1.5        # static/breakaway torque


class InductionMotor:
    """Stateful simulation of one induction motor + its mechanical load."""

    def __init__(self, params: MotorParameters = None):
        self.p = params or MotorParameters()
        self.pole_pairs = self.p.poles / 2

        # state
        self.omega_m = 0.0          # mechanical speed, rad/s
        self.winding_temp_c = self.p.ambient_temp_c
        self.time_s = 0.0
        self.tripped = False
        self.trip_reason = None

        # last computed electrical quantities (for telemetry)
        self.applied_freq_hz = 0.0
        self.slip = 1.0
        self.torque_e_nm = 0.0
        self.torque_load_nm = 0.0
        self.i_stator_a = 0.0
        self.i_rotor_a = 0.0

    def reset_trip(self):
        self.tripped = False
        self.trip_reason = None

    def _equivalent_circuit(self, freq_hz: float, omega_m: float):
        """Solve the per-phase equivalent circuit at a given supply frequency
        and mechanical speed. Uses scalar V/f control: terminal voltage is
        scaled proportionally to frequency (constant flux) below base freq,
        which is exactly how an industrial VFD drives the motor."""
        p = self.p
        if freq_hz <= 0.01:
            return 0.0, 0.0, 0.0, 0.0, 1.0

        # V/f scaling (constant volts/hertz below rated frequency)
        freq_ratio = freq_hz / p.rated_freq_hz
        v1 = p.rated_voltage_ln * min(freq_ratio, 1.0)

        omega_sync = 2 * math.pi * freq_hz / self.pole_pairs
        slip = (omega_sync - omega_m) / omega_sync
        # avoid singularity at s=0 (no-load, synchronous speed)
        if abs(slip) < 1e-4:
            slip = 1e-4 if slip >= 0 else -1e-4

        # reactances scale linearly with applied frequency
        x1 = p.x1 * freq_ratio
        x2 = p.x2 * freq_ratio
        xm = p.xm * freq_ratio
        r1, r2 = p.r1, p.r2

        # Thevenin equivalent seen by the rotor branch
        denom_r = r1
        denom_x = x1 + xm
        z_mag_sq = denom_r ** 2 + denom_x ** 2
        v_th = v1 * xm / math.sqrt(z_mag_sq)

        # Z_th = jXm * (R1 + jX1) / (R1 + j(X1+Xm))
        # Z_th derived via explicit complex arithmetic (avoids a cmath import):
        # (R1 + jX1) * jXm = jXm*R1 - Xm*X1
        num_re = -xm * x1
        num_im = xm * r1
        # divide by (R1 + j(X1+Xm))
        r_th = (num_re * denom_r + num_im * denom_x) / z_mag_sq
        x_th = (num_im * denom_r - num_re * denom_x) / z_mag_sq

        r2_over_s = r2 / slip
        z_total_sq = (r_th + r2_over_s) ** 2 + (x_th + x2) ** 2

        i2 = v_th / math.sqrt(z_total_sq)  # rotor current referred to stator
        i_m = v1 / xm if xm > 1e-6 else 0.0  # magnetizing branch current (approx)
        # stator current: magnetizing + rotor branch (approx magnitude sum,
        # good enough for telemetry realism without full phasor tracking)
        i1 = math.sqrt(i_m ** 2 + i2 ** 2)

        n_sync_elec = omega_sync  # electrical sync speed = mech sync speed here (already /pole_pairs)
        torque_e = (3 * v_th ** 2 * r2_over_s) / (omega_sync * z_total_sq)

        return torque_e, i1, i2, omega_sync, slip

    # Electromechanical dynamics near synchronous speed are stiff (torque is
    # extremely sensitive to slip when slip is small). A single large Euler
    # step there will overshoot synchronous speed and diverge. We integrate
    # in small fixed sub-steps instead -- this is the standard fix for stiff
    # ODEs without reaching for a full implicit solver.
    _SUBSTEP_S = 0.0005  # 0.5 ms

    def step(self, dt: float, applied_freq_hz: float):
        """Advance the simulation by dt seconds at the given applied stator
        frequency (this is what the VFD is ACTUALLY outputting right now --
        after any ramp-limiting or, in an attack scenario, without it)."""
        if self.tripped:
            applied_freq_hz = 0.0
        applied_freq_hz = max(0.0, applied_freq_hz)

        n_sub = max(1, int(round(dt / self._SUBSTEP_S)))
        dt_sub = dt / n_sub

        torque_e = i1 = i2 = omega_sync = slip = 0.0
        for _ in range(n_sub):
            torque_e, i1, i2, omega_sync, slip = self._equivalent_circuit(
                applied_freq_hz, self.omega_m
            )

            torque_load = (
                self.p.load_static_nm * min(self.omega_m, 1.0)
                + self.p.load_k_quadratic * self.omega_m ** 2
            )
            friction = self.p.friction_coeff * self.omega_m
            net_torque = torque_e - torque_load - friction

            domega = (net_torque / self.p.inertia_kgm2) * dt_sub
            self.omega_m = max(0.0, self.omega_m + domega)

            # thermal: copper losses drive temperature rise toward a steady state
            p_loss = 3 * (i1 ** 2 * self.p.r1 + i2 ** 2 * self.p.r2)
            dtemp = (
                (p_loss - (self.winding_temp_c - self.p.ambient_temp_c) / self.p.thermal_resistance)
                / self.p.thermal_capacitance
            ) * dt_sub
            self.winding_temp_c += dtemp

            if self.winding_temp_c >= self.p.trip_temp_c and not self.tripped:
                self.tripped = True
                self.trip_reason = "overtemperature"
                applied_freq_hz = 0.0

        self.time_s += dt
        self.applied_freq_hz = applied_freq_hz
        self.slip = slip
        self.torque_e_nm = torque_e
        self.torque_load_nm = (
            self.p.load_static_nm * min(self.omega_m, 1.0)
            + self.p.load_k_quadratic * self.omega_m ** 2
        )
        self.i_stator_a = i1
        self.i_rotor_a = i2

        return self.snapshot()

    @property
    def rpm(self) -> float:
        return self.omega_m * 60.0 / (2 * math.pi)

    @property
    def sync_rpm(self) -> float:
        if self.applied_freq_hz <= 0:
            return 0.0
        return (120.0 * self.applied_freq_hz) / self.p.poles

    def snapshot(self) -> dict:
        return {
            "time_s": round(self.time_s, 3),
            "applied_freq_hz": round(self.applied_freq_hz, 3),
            "rpm": round(self.rpm, 1),
            "sync_rpm": round(self.sync_rpm, 1),
            "slip": round(self.slip, 4),
            "torque_e_nm": round(self.torque_e_nm, 2),
            "torque_load_nm": round(self.torque_load_nm, 2),
            "i_stator_a": round(self.i_stator_a, 2),
            "i_rotor_a": round(self.i_rotor_a, 2),
            "winding_temp_c": round(self.winding_temp_c, 1),
            "tripped": self.tripped,
            "trip_reason": self.trip_reason,
        }
