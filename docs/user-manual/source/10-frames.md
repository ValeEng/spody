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
  Reference Frame), realised by the positions of extragalactic
  radio sources (Kaplan 2005 summarises the IAU resolutions that
  define it). Its axes agree with the mean equator
  and dynamical equinox of J2000 to within about 0.02 arcseconds
  (the *frame bias*), so for most purposes X points toward the
  J2000 equinox, Z toward the J2000 mean pole of the Earth, and Y
  completes the right-handed triad. SPICE's `J2000` frame and the
  CCSDS `EME2000` are the same axes to that level.
- **Right-handed.**
- **No rotation** with respect to the distant background of
  galaxies; the central body orbits, librates / rotates *inside*
  this frame.

Every binary output is in this frame, and every initial state ends up
in it: a `[initial_state]` written in another frame
(`central_body_fixed`, `central_body_fixed_rotating`, `orbit_plane`,
or as Keplerian elements, chapter 6) is converted once, before the
first step. During the run the engine also rotates into the body-fixed
frame wherever a force needs it (the gravity field, the atmosphere,
the tide-raising bodies).

> If you have a state vector in a frame SpOdy does not accept (an
> ecliptic frame, the frame of another body), convert it to the
> central-body-centred ICRF before pasting it into the TOML. The
> SPICE toolkit's `spkez` plus `pxform` calls are the canonical
> way to do this; the SpOdy bundle does not include a SPICE
> wrapper. TLE or OMM elements go through `spody convert gp`
> (chapter 12).
>
> One exception is **batch input in a rotating frame**: if your
> cases CSV is a snapshot of a debris cloud (or any collection of
> objects whose deltas are measured against a reference
> satellite's local axes), the GUI rotates the state columns to
> ICRF automatically when the TOML is saved and emits `cases_file`
> pointing at the rotated copy. Two rotating frames are supported
> via the `cases_frame` combo: **RIC** (radial / in-track /
> cross-track) and **LVLH** (local vertical / local horizontal,
> nadir-pointing). The TOML carries `cases_frame` and `cases_source_file`
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
lunar libration angles that DE440 carries among its Chebyshev series
(Park et al. 2021):

    C_ICRF→PA = Rz(psi) · Rx(theta) · Rz(phi)

with `(phi, theta, psi)` the 3-1-3 Euler angles of the lunar
mantle at the requested ET and `Rx`, `Rz` the frame (passive)
rotations about the x and z axes. The convention is
`r_PA = C · r_ICRF`.

GRGM1200B was estimated in the PA frame of DE430, the ephemeris of its
time; SpOdy rotates it with the PA frame of DE440. The two frames
differ by 0.14&Prime; (0.16&Prime; at most), 1.4 m on the lunar
surface; on the LRO benchmark (chapter 15) the choice changes the
6-day trajectory by 0.36 m.

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
conventions in their CIO-based form (Capitaine & Wallace 2006; IERS
Conventions 2010, Petit & Luzum 2010, ch. 5) plus the IERS
Earth-Orientation Parameters (EOP):

    R_GCRS→ITRS = W(t) · R3(+ERA(t)) · Q(t)

where `Q(t)` is the celestial-to-intermediate frame matrix
assembled from the IAU 2006 X, Y, s+XY/2 series (the
`tab5.2{a,b,d}.txt` files the wizard downloads; evaluated on an
hourly grid and interpolated with a cubic, 3&times;10<sup>-7</sup> mas)
plus the IERS celestial-pole offsets dX, dY, `ERA(t)` is the
Earth Rotation Angle driven by UT1 (which IERS publishes as
`UT1 - UTC` in `finals2000A.all`), and `W(t)` is the polar-motion
matrix built from the (`xp`, `yp`) angles in the same EOP file.
The daily EOP rows are interpolated linearly. On a leap-second day,
`UT1 - UTC` jumps by +1 s between that row and the next while UT1
itself is continuous, so SpOdy interpolates `UT1 - TAI` (the IERS
convention) and the jump happens at 23:59:60 UTC, where it belongs,
instead of being spread over the whole day.
The chain follows the IERS / SOFA procedure step for step; the
hourly interpolation of X, Y, s and the series read from the IERS
tables keep it within about 0.02 mas of SOFA's own evaluation (the
`spopy` comparison below).

The wizard manages the two raw data sets (`finals2000A.all` plus
the IAU 2006 series tables); chapter 3 covers the
startup-freshness check that nudges you to refresh
`finals2000A.all` after IERS publishes a new bulletin.

The same rotation pipeline is exposed in pure Python through the
bundled `spopy` package (`spopy.MappedEOP` for the IERS table
reader, `spopy.icrf_to_itrs(et, eop)` for the rotation at one epoch
and `spopy.icrf_to_itrs_many(et_array, eop)` for many at once). It
follows the engine's chain step for step, building on `pyerfa`: the
two agree to about 3 mm at GNSS radius (0.02 mas; the series come
from the IERS tables in the engine and from SOFA in Python), which
is what the GUI uses for body-fixed plots and for the body-fixed
initial states the Run form writes.

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
orbit. The frame is also called the **RSW frame** (Vallado 2013),
**RTN** (radial / transverse / normal, the name used by the CCSDS
orbit and conjunction messages, CCSDS 502.0-B-3) or the **Hill
frame** (in the context of the Clohessy-Wiltshire linearised
equations, Clohessy & Wiltshire 1960).

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
compute it from the SpOdy diff RIC output: LVLH x is the in-track
component, LVLH y minus the cross-track one, LVLH z minus the radial
one. SpOdy does not provide an LVLH plot directly because RIC (RTN)
is the conventional choice in conjunction-assessment and
orbit-regression work, and a reordered, sign-flipped duplicate would
be just visual noise.

LVLH is **supported on the batch-input side**, however: the GUI's
`cases_frame` combo accepts `lvlh` and applies the same
rotate-to-ICRF-at-save pipeline as `ric`, with the axes of
rendezvous and attitude work (`z = -r_hat`, `y = -h_hat`,
`x = y x z`; Fehse 2003). A cases file whose offsets are written in
those axes can be fed into SpOdy without a manual rotation; one
written in RTN axes (a CCSDS message, for instance) goes in as `ric`.

## The synodic rotating frame (CR3BP)

Under `dynamics_model = "cr3bp"` the state lives in the classic
**synodic rotating frame** of the restricted problem (Szebehely 1967): origin at the barycenter, x-axis along
primary&nbsp;1 &rarr; primary&nbsp;2, z along the orbital angular
momentum, rotating at the constant
&omega; = &radic;((&mu;&#8321;+&mu;&#8322;)/L&sup3;). The primaries
sit frozen on the x-axis (primary&nbsp;1 at
&minus;&mu;&#8322;/(&mu;&#8321;+&mu;&#8322;)&middot;L,
primary&nbsp;2 at +&mu;&#8321;/(&mu;&#8321;+&mu;&#8322;)&middot;L).
By convention the synodic axes coincide with the run's inertial
axes at t&nbsp;=&nbsp;0 &mdash; the engine and
`spopy.inertial_to_synodic` / `synodic_to_inertial` share this
t&nbsp;=&nbsp;0 identity.

### The instantaneous pulsating variant (From CR3BP&hellip;)

The idealised frame above assumes circular primary motion at the
fixed separation L. The **From CR3BP&hellip;** dialog (chapter 5),
which seeds a *high-fidelity* run from a CR3BP catalog state, uses
the **instantaneous pulsating** variant instead: at the requested
epoch it reads the *real* primary geometry from the ephemeris
&mdash; actual separation l\*, axes built from the actual relative
position and velocity, angular rate &omega; = h/l\*&sup2; along
the actual orbit normal, plus the radial rate l&#775;\* &mdash;
and maps the nondimensionalised catalog state through it. Two
properties make this the right bridge between the two worlds:
both primaries land **exactly** on their DE440 states, and the
transform degenerates to the t&nbsp;=&nbsp;0 identity convention
when the real orbit is replaced by the circular idealisation.

Because a CR3BP orbit is periodic only in the CR3BP, the mapped
state is a **seed**, not a bound orbit: the full-force propagation
shadows the catalog orbit for a while (a 9:2 NRHO holds a few
revolutions) and then departs. That divergence is the physics of
the ephemeris model, not a conversion error.

## Classical orbital elements

The classical Keplerian elements are reported in the **central-
body inertial frame**, with the active central body's
gravitational parameter `mu` baked in:

| Central body | `mu` (km&sup3;/s&sup2;)        | Source     |
|--------------|---------------------------------|------------|
| Moon         | 4902.8001184575496              | DE440 (Park et al. 2021) |
| Earth        | 398600.4415                     | EIGEN-6C4 (Förste et al. 2014); the TT-compatible value of the IERS Conventions 2010 |

Both constants live alongside the central-body radii in
`spody_const.h` (single source of truth, shared between the C
engine and the Python GUI).

The two-body term of a run **with a gravity file** does not use
these: it takes the GM declared by the file, because normalized
coefficients hold only with the GM they were estimated with. For
the Earth the two coincide to the bit. For the Moon, GRGM1200B
declares 4902.8001224453, 8e-10 from the DE440 value &mdash; two
estimates of the same quantity, within the uncertainty of both.
Orbital elements, at input (`kind = "keplerian"`) and in the
plots, are a representation of the state: they are always read
and shown with the body's GM from the table above, the same both
ways, so what you type is what you get back. The field's GM is a
parameter of the gravity model only. The run log prints the GM of
the central term on its `gravity` line.

### Reference plane

Inclination, RAAN, and the argument of periapsis are reported
relative to the **inertial XY plane** (the ICRF XY plane,
projected at the central body's centre). This is the ICRF
equator, *not* the central body's equator.

- For **Moon-centred** runs the lunar spin axis is tilted about
  1.5&deg; from the ecliptic pole (Cassini's laws), and the ecliptic
  is tilted 23.4&deg; from the ICRF XY plane, so the lunar equator is
  tilted between about 22&deg; and 25&deg; from the ICRF XY plane over
  the 18.6-year cycle of the lunar node. An orbit that is polar with
  respect to the Moon's own pole has its normal in the lunar equator
  and therefore shows an ICRF inclination anywhere between about
  65&deg; and 115&deg;, depending on where its node lies.
- For **Earth-centred** runs the difference is much smaller: the
  ICRF XY plane is the mean equator of J2000 (to the 0.02&Prime;
  frame bias), and the true equator of date moves away from it by
  precession, about 20&Prime; per year in the direction of the pole
  (0.14&deg; by 2026), plus nutation of up to about 9&Prime;. A polar
  Earth orbit therefore shows an inclination within a few tenths of
  a degree of 90&deg;.

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
to the Sun at the typed epoch with a **low-precision analytic
model**: the Sun's mean longitude and mean anomaly, the equation of
the centre to two terms and the mean obliquity, as in the
low-accuracy solar position of Meeus (1998, ch. 25). The direction
is the geocentric one (from a Moon-centred scene the parallax is
under 0.2&deg;), and the longitudes are referred to the equinox of
date, so the arrow is off the ICRF direction by the precession since
J2000 (about 0.37&deg; in 2026) plus about 0.01&deg; of the series:
enough for a visual arrow, not for any computation.

The arrow direction is rendered in the same `central_inertial`
frame as the rest of the scene, so it lines up with the orbit
polylines to the accuracy above. The arrow length is purely visual
(scaled to the central body's radius); it does *not* represent
astronomical distance.

For propagation purposes the engine uses the full-precision DE440
ephemeris &mdash; the analytic Sun direction is used only for the
viewport arrow, never for any force computation.
