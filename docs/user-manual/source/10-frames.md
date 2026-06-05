# Frame conventions

A great many sign and orientation mistakes in orbital work come
down to differing frame conventions. SpOdy uses a single small
set of frames, all spelt out here.

## The central-body inertial frame

The propagation frame is the **central body's inertial frame**,
which the schema spells `central_inertial`. For a Moon-centred
propagation (the only central body supported in this release):

- **Origin** at the centre of the Moon.
- **Axes** aligned with the ICRF (International Celestial
  Reference Frame), which is the modern realisation of the J2000
  inertial axes. In practice the X axis points toward the dynamical
  equinox of J2000, the Z axis aligns with the J2000 mean rotation
  pole of Earth, and Y completes the right-handed triad.
- **Right-handed.**
- **No rotation** with respect to the distant background of
  galaxies; the Moon orbits, librates, and rotates *inside* this
  frame.

All TOML inputs (`initial_state.position_km`, `velocity_kms`) and
all binary outputs are in this frame. The engine does not perform
any frame conversions internally beyond the conversions needed to
evaluate body-fixed gravity harmonics at each step.

> If you have a state vector in a different frame (Earth-centred
> J2000, Moon's body-fixed frame, an ecliptic frame), convert it
> to the lunar-centred ICRF before pasting it into the TOML. The
> SPICE toolkit's `spkez` plus `pxform` calls are the canonical
> way to do this; the SpOdy bundle does not include a SPICE
> wrapper.
>
> One exception is **batch input in RIC**: if your cases CSV is a
> sensor-frame snapshot of a debris cloud (or any collection of
> objects whose deltas are measured in the radial / in-track /
> cross-track basis of a reference satellite), the GUI rotates the
> state columns to ICRF automatically at Generate-TOML. The C
> engine never sees the RIC schema. See chapter 7
> ("RIC-frame batch input") for the workflow.

## The body-fixed frame

Internally, the engine evaluates the spherical-harmonic gravity
expansion in the **Moon's principal-axis body-fixed frame**, which
is the frame the GRGM1200B coefficient set is expressed in. The
rotation from inertial to body-fixed is constructed at every step
from the planetary ephemeris (rotation matrix from DE440's lunar
attitude data). Users never see this frame: the inputs and
outputs stay in the inertial frame, and the body-fixed conversion
happens transparently inside the harmonics evaluation.

This is the relevant point for users: even though the engine uses
the body-fixed frame in the harmonics math, you do **not** need to
worry about libration angles, principal-axis vs mean-Earth axes,
or sidereal-time alignment in your inputs. The conversion is
exact for every step using the same source data NASA uses for the
GRGM1200B model.

## The RIC frame (RIC = Radial / In-track / Cross-track)

This is the frame used by the **diff RIC** plot. It is built **on
the fly at every sample** from the state vector of trajectory A:

| Axis            | Definition                              |
|-----------------|------------------------------------------|
| **Radial**      | `r_hat_A = r_A / |r_A|`, positive outward |
| **Cross-track** | `c_hat_A = (r_A × v_A) / |r_A × v_A|`, positive along the orbit normal |
| **In-track**    | `i_hat_A = c_hat × r_hat_A`, completes the right-handed frame |

The frame is right-handed; the axes are mutually orthogonal at
every sample, but their inertial orientation rotates with the
orbit. The frame is also called the **RSW frame** (Vallado's
nomenclature) or the **Hill frame** (in the context of the
Clohessy-Wiltshire linearised equations).

### What goes where

When you decompose a position-error vector `Δr = r_A - r_B` onto
this frame:

- A positive **radial** component means trajectory A is higher
  (farther from the central body) than B at that instant.
- A positive **in-track** component means A is *ahead* of B along
  the orbit. Persistent growth of `+` in-track is the canonical
  signal of a small energy / mean-motion drift between the two
  propagations.
- A positive **cross-track** component means A is offset out of
  B's instantaneous orbital plane along the orbit normal direction.

### Comparison with LVLH

The Local Vertical / Local Horizontal frame (LVLH), used in
spacecraft attitude work, is *related to but not identical with*
the RIC frame. The signs differ:

| | RIC (SpOdy)                | LVLH |
|---|----------------------------|------|
| Z / W | `+r_hat`, outward       | `-r_hat`, nadir (toward central body) |
| X / I | `c_hat × r_hat` (≈ `+v_hat` for circular orbits) | ≈ `+v_hat` |
| Y / C | `+h_hat` (orbit normal) | `-h_hat` |

If you need an LVLH decomposition you can compute it from the
SpOdy diff RIC output by flipping the radial and cross-track
signs. SpOdy does not provide an LVLH plot directly because RIC
is the conventional choice in conjunction-assessment and orbit-
regression work, and a sign-flipped duplicate plot would be just
visual noise.

## Classical orbital elements

The classical Keplerian elements are reported in the **central-
body inertial frame**, with the central body's gravitational
parameter `mu` baked in. For the Moon today, `mu = 4902.800066
km^3/s^2`. This number lives in the GUI source code as
`MU_MOON_KM3_S2` and is the same value used in the C engine for
the two-body reference acceleration.

### Reference plane

Inclination, RAAN, and the argument of periapsis are reported
relative to the **inertial XY plane** (the ICRF XY plane,
projected at the Moon's centre). This is *not* the Moon's
equatorial plane: the lunar pole is tilted ~5.1&deg; from the
ecliptic normal, and the ICRF Z axis aligns with Earth's mean pole,
so the lunar equator is tilted ~24&deg; from the ICRF XY plane.

A consequence: a near-polar lunar orbit (which is "polar" with
respect to the Moon's body-fixed pole) appears at `i ≈ 85–115°`
in SpOdy's elements, not at exactly 90°, because the reference
plane is the ICRF and not the lunar equator.

This is the conventional choice and matches the SPICE convention
when querying the LRO ephemeris in J2000. If you specifically need
elements relative to the Moon's equator, post-process the inertial
state vectors externally; SpOdy does not currently offer this view.

### Quadrant resolution

The right quadrants are picked from the standard sign tests:

- **RAAN**: `Ω` is in `[0, π]` when the Y component of the node
  line is non-negative, in `(π, 2π)` otherwise.
- **Argument of periapsis**: `ω` is in `[0, π]` when the Z
  component of the eccentricity vector is non-negative, in
  `(π, 2π)` otherwise.
- **True anomaly**: `ν` is in `[0, π]` when `r · v ≥ 0` (i.e.
  moving away from periapsis), in `(π, 2π)` otherwise.

## Sun direction (3D viewer)

The `+ Sun arrow` button in the 3D viewer computes the direction
to the Sun **from the central body's centre** at the typed epoch,
using a **low-precision analytic model** (Meeus-style series for
the solar ecliptic longitude and the obliquity of the ecliptic,
sufficient for visualisation accuracy of arc-minute level).

The arrow direction is rendered in the same `central_inertial`
frame as the rest of the scene, so it lines up correctly with the
orbit polylines. The arrow length is purely visual (scaled to the
Moon sphere's radius); it does *not* represent astronomical
distance.

For propagation purposes the engine uses the full-precision DE440
ephemeris &mdash; the analytic Sun direction is used only for the
viewport arrow, never for any force computation.
