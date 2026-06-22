# Frame conventions

A great many sign and orientation mistakes in orbital work come
down to differing frame conventions. SpOdy uses a single small
set of frames, all spelt out here.

## The central-body inertial frame

The propagation frame is the **central body's inertial frame**,
which the schema spells `central_inertial`. For both supported
central bodies (Moon and Earth):

- **Origin** at the centre of the central body.
- **Axes** aligned with the ICRF (International Celestial
  Reference Frame), which is the modern realisation of the J2000
  inertial axes. In practice the X axis points toward the dynamical
  equinox of J2000, the Z axis aligns with the J2000 mean rotation
  pole of Earth, and Y completes the right-handed triad.
- **Right-handed.**
- **No rotation** with respect to the distant background of
  galaxies; the central body orbits, librates / rotates *inside*
  this frame.

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
> One exception is **batch input in a rotating frame**: if your
> cases CSV is a snapshot of a debris cloud (or any collection of
> objects whose deltas are measured against a reference
> satellite's local axes), the GUI rotates the state columns to
> ICRF automatically at Generate-TOML and emits `cases_file`
> pointing at the rotated copy. Two rotating frames are supported
> via the `cases_frame` combo: **RIC** (radial / in-track /
> cross-track, the typical chaser sensor frame) and **LVLH**
> (NASA / Goddard nadir-pointing convention, common in breakup
> models). The TOML carries `cases_frame` and `cases_source_file`
> as GUI-only round-trip metadata; `spody.exe` ignores them. See
> chapter 7 ("Rotating-frame batch input (RIC / LVLH)") for the
> workflow.

## The body-fixed frames

Internally the engine evaluates the spherical-harmonic gravity
expansion in the **central body's body-fixed frame**, which is
the frame the coefficient set is expressed in. The rotation from
inertial to body-fixed is rebuilt at every step from the right
source for the active central body.

### Moon: Principal Axes (PA)

For Moon-centred runs the body-fixed frame is the **lunar
Principal Axes (PA)** frame, which is the frame the GRGM1200B
coefficient set is expressed in. The rotation is built from the
lunar libration angles that DE440 carries in its slot 12:

    C_ICRF→PA = Rz(psi) · Rx(theta) · Rz(phi)

with `(phi, theta, psi)` the 3-1-3 Euler angles of the lunar
mantle at the requested ET. The convention is `r_PA = C · r_ICRF`.

The PA frame **does become visible** in the Analysis tab's
*impact* views (chapter 9): the impact lat/lon map (both
projections), the density heatmap, and the 3D impact scene all
project IMPACT events from ICRF onto PA so the latitude and
longitude axes refer to the actual lunar surface. The same
rotation pipeline is reused there, this time exposed in pure
Python through the bundled `spopy` package
(`spopy.Ephemeris.lunar_libration_angles` +
`spopy.icrf_to_moon_pa`), which is a numpy-only re-implementation
of the spody-core C helpers (bit-identical, validated at the time
of landing).

### Earth: International Terrestrial Reference System (ITRS)

For Earth-centred runs the body-fixed frame is the
**International Terrestrial Reference System (ITRS)**, the
co-rotating Earth-fixed frame realised by the ITRF datums (for
practical purposes, ITRF2014 / 2020). The rotation from inertial
to body-fixed follows the **IAU 2006/2000A_R06** Earth-orientation
conventions plus the IERS Earth-Orientation Parameters (EOP):

    R_GCRS→ITRS = W(t) · R3(+ERA(t)) · Q(t)

where `Q(t)` is the celestial-to-intermediate frame matrix
assembled from the IAU 2006 X, Y, s+XY/2 series (the
`tab5.2{a,b,d}.txt` files the wizard downloads), `ERA(t)` is the
Earth Rotation Angle driven by UT1 (which IERS publishes as
`UT1 - UTC` in `finals2000A.all`), and `W(t)` is the polar-motion
matrix built from the (`xp`, `yp`) angles in the same EOP file.
The chain is exact at the SOFA / ERFA precision floor (sub-mas).

The wizard manages the two raw data sets (`finals2000A.all` plus
the IAU 2006 series tables); chapter 3 covers the
startup-freshness check that nudges you to refresh
`finals2000A.all` after IERS publishes a new bulletin.

The same rotation pipeline is exposed in pure Python through the
bundled `spopy` package (`spopy.MappedEOP` for the IERS table
reader plus `spopy.icrf_to_itrs(et, eop)` for the rotation
itself). The Python implementation wraps `pyerfa` and matches the
C engine at machine epsilon.

### Common to both bodies

For the **propagation itself** users do not see the body-fixed
frame: the inputs and outputs stay in the inertial frame, and the
body-fixed conversion happens transparently inside the harmonics
evaluation. You do **not** need to worry about libration angles,
principal-axis vs mean-Earth axes, sidereal time, or polar motion
in your inputs.

The active body-fixed triad is also drawn in the **3D orbit** plot
(chapter 9) and animated along with the trajectory: PA libration
for the Moon, IAU 2006 + EOP rotation for the Earth, so the
textured body and the triad stay locked to a consistent
orientation at every animation frame.

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

If you need an LVLH decomposition of a **diff plot** you can
compute it from the SpOdy diff RIC output by flipping the radial
and cross-track signs. SpOdy does not provide an LVLH plot
directly because RIC is the conventional choice in conjunction-
assessment and orbit-regression work, and a sign-flipped duplicate
plot would be just visual noise.

LVLH is **fully supported on the batch-input side**, however: the
GUI's `cases_frame` combo accepts `lvlh` and applies the same
rotate-to-ICRF-at-Generate pipeline as `ric`, with the
NASA / Goddard sign convention (`z = -r_hat`, `y = -h_hat`,
`x = y x z`). This is the convention used by the NASA breakup
model, by CCSDS conjunction messages, and by the rest of the
debris-evolution-tool ecosystem &mdash; so a binary export from
those tools can be fed into SpOdy without a manual sign flip.

## Classical orbital elements

The classical Keplerian elements are reported in the **central-
body inertial frame**, with the active central body's
gravitational parameter `mu` baked in:

| Central body | `mu` (km&sup3;/s&sup2;)        | Source     |
|--------------|---------------------------------|------------|
| Moon         | 4902.800066                     | GRGM1200B  |
| Earth        | 398600.4415                     | EIGEN-6C4  |

Both constants live alongside the central-body radii in
`spody_const.h` (single source of truth, shared between the C
engine and the Python GUI), and are the same values used by the
two-body reference acceleration at every step.

### Reference plane

Inclination, RAAN, and the argument of periapsis are reported
relative to the **inertial XY plane** (the ICRF XY plane,
projected at the central body's centre). This is the ICRF
equator, *not* the central body's equator.

- For **Moon-centred** runs the lunar pole is tilted ~5.1&deg;
  from the ecliptic normal, so the lunar equator is tilted
  ~24&deg; from the ICRF XY plane. A near-polar lunar orbit
  (which is "polar" with respect to the Moon's body-fixed pole)
  appears at `i ≈ 85–115°` in SpOdy's elements, not at exactly
  90°, because the reference plane is the ICRF and not the
  lunar equator.
- For **Earth-centred** runs the difference is much smaller:
  the J2000 mean equatorial plane *is* (by definition) the
  ICRF XY plane up to the &sim;arcsec-level precession /
  nutation that the rotation pipeline handles separately. A
  near-polar Earth orbit sits very close to `i = 90°` in
  SpOdy's elements, drifting only by tens of milliarcseconds
  per year from precession.

This is the conventional choice and matches the SPICE convention
when querying the LRO or GLONASS ephemerides in J2000. If you
specifically need elements relative to the central body's
equator, post-process the inertial state vectors externally;
SpOdy does not currently offer this view.

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
