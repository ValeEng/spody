# Uncertainty propagation (Monte Carlo)

A propagation is only as good as what goes into it. The initial
state comes from an orbit determination with its own error; the drag
coefficient, the area, the mass and the density are known to some
percent at best. This chapter shows how SpOdy carries those
uncertainties forward in time: **it propagates many copies of the
scenario, each with the uncertain quantities drawn at random, and
measures how the copies spread**. That is the Monte Carlo method,
the reference against which every faster method (linear covariance,
unscented transform) is checked.

The command is

```
spody uncertainty montecarlo <name>.uq.toml
```

It reads a small file of its own (the *uncertainty file*, always
named `<name>.uq.toml`, where *uq* stands for *uncertainty
quantification*) that points to an ordinary scenario TOML and says
what is uncertain and by how much. The scenario itself is not
touched. SpOdy only propagates the uncertainty; it does not estimate
the orbit from measurements.

## A first run

Scenario `iss.toml` (any high-fidelity, fixed-output scenario), and
next to it `iss.uq.toml`:

```toml
[montecarlo]
name        = "iss_mc"
scenario    = "iss.toml"
samples     = 1000
seed        = 1
thread_number = 8
snapshots_s = [86400.0, 172800.0]

[montecarlo.initial_state]
axes               = "ric"
position_sigma_km  = [0.05, 0.2, 0.03]
velocity_sigma_kms = [2.0e-4, 5.0e-5, 3.0e-5]

[montecarlo.parameters]
"spacecraft.drag.Cd" = { distribution = "lognormal", sigma_percent = 20.0, scenario_value_is = "mean" }
```

```
spody uncertainty montecarlo iss.uq.toml
```

The run folder (`<output_dir>/<UTC>/`, as for every run) then holds
the nominal trajectory, the statistics at every output epoch, the
clouds at the two snapshots, a readable sigma table, the impacts and
the log. The rest of the chapter explains each piece.

## The uncertainty file

The file has one table, `[montecarlo]`, and two optional
sub-tables. The schema is **closed**: an unknown key is an error, not
a silently ignored line. At least one of the two sub-tables must be
present (otherwise there is nothing to disperse).

The two kinds of file never mix: `spody propagate`, `spody batch`
and `spody validate` refuse a scenario that contains `[montecarlo]`,
and `spody uncertainty montecarlo` refuses a file that is not named
`*.uq.toml` or has no `[montecarlo]` table.

### `[montecarlo]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | required | Names every output file. Must be a valid file name. |
| `scenario` | string (path) | required | The scenario TOML, relative to the `.uq.toml` or absolute. Must be `high_fidelity`, without `[batch]`, with `output.mode = "fixed"` (every case on the same epochs). |
| `samples` | integer &ge; 2 | required | N, the number of dispersed cases. The nominal (case 0) comes on top. |
| `seed` | integer &ge; 0 | required | The random seed. Same file, same seed: same cases, bit for bit, on any machine and any number of threads. |
| `output_dir` | string (path) | the scenario's `output.output_dir` | Parent of the run folder. Absent or empty: the scenario's. |
| `thread_number` | integer &ge; 1 | 1 | Cases propagated in parallel (OpenMP build). The results do not depend on it. |
| `snapshots_s` | array of seconds | none | Epochs where the whole cloud is saved, case by case. Each must be an output epoch of the scenario (a multiple of `interval_s`, or the end). At most 64. |
| `case_outputs` | bool | false | Also write each case's trajectory (`SPDYOUT_`). Off by default: 1000 cases would mean 1000 files. |

### `[montecarlo.initial_state]`: the state error

The error of the initial position and velocity, as a Gaussian with
zero mean. Give it **either** as standard deviations (and optionally
correlations) **or** as a full covariance:

| Key | Type | Meaning |
|---|---|---|
| `axes` | `"ric"` or `"icrf"` | Required. The axes the numbers are written in. RIC = radial, in-track, cross-track of the scenario's initial state (r&#770; = r/\|r\|, c&#770; = r&times;v/\|r&times;v\|, &icirc; = c&#770;&times;r&#770;; rotation only, the CCSDS RTN convention). |
| `position_sigma_km` | 3 numbers &ge; 0 | &sigma; of the three position components [km]. |
| `velocity_sigma_kms` | 3 numbers &ge; 0 | &sigma; of the three velocity components [km/s]. |
| `correlation` | 6&times;6 | Optional correlation matrix (symmetric, 1 on the diagonal, entries in [&minus;1, 1]). Identity if absent. |
| `covariance` | 6&times;6 | The covariance itself [km&sup2;, km&sup2;/s, km&sup2;/s&sup2;], instead of the three keys above. |

A component with zero &sigma; stays exact. The matrix must be
positive definite on the other components; if it is not, the error
gives the most negative eigenvalue of the correlation matrix and its
direction, so you can see which correlations are inconsistent.

Each case's offset is drawn as L&middot;z, with L the Cholesky
factor of the covariance and z six independent standard normal
numbers, then rotated from RIC to ICRF when `axes = "ric"`, and added
to the scenario's initial state (resolved to ICRF first, whatever
frame the scenario uses).

### `[montecarlo.parameters]`: uncertain parameters

One line per uncertain parameter, keyed by its **batch target path**
(the same dotted paths as `[batch.columns]`, chapter 7):

```toml
[montecarlo.parameters]
"spacecraft.drag.Cd"        = { distribution = "lognormal", sigma_percent = 20.0, scenario_value_is = "mean" }
"spacecraft.srp.Cr"         = { distribution = "normal", sigma = 0.05 }
"force_model.density_scale" = { distribution = "lognormal", sigma_ln = 0.25, scenario_value_is = "median" }
```

The scenario value p&#8320; is the centre of the distribution. At most
16 parameters. A target is refused when it cannot be uncertain or
would have no effect: the initial state (use the table above),
`simulation.*`, `integrator.*`, `output.*`, a drag
parameter with drag off, a radiation-pressure parameter with neither
SRP nor Earth radiation pressure on, `spacecraft.mass_kg` when no
force depends on the mass, `density_scale` when the scenario uses a
`density_scale_file`, a `debris.*` target in a spacecraft scenario
and vice versa.

**Normal** (`distribution = "normal"`): p = p&#8320; + &sigma; z with
`sigma` in the parameter's units, or p = p&#8320;(1 + r z) with
`sigma_percent` = 100 r. Mean and median are both p&#8320;. A normal
has unbounded tails: for a parameter that must stay positive, the
probability of a negative draw is &Phi;(&minus;1/r) &mdash; 7.6e-24
at 10 %, 4.3e-4 at 30 %, 2.3 % at 50 %. If any drawn case falls
outside the parameter's physical domain the run is **refused**,
naming the cases; nothing is discarded silently.

**Lognormal** (`distribution = "lognormal"`): p = p&#8320; X with
ln X normal, so p is always positive &mdash; the natural choice for
multiplicative factors (Cd, area, density scale). The spread is
`sigma_ln` (the &sigma; of ln X; a 1-&sigma; factor of e^&sigma;) or
`sigma_percent` (the relative standard deviation r of X; then
&sigma;&#8343;&#8345; = &radic;ln(1 + r&sup2;)). For a lognormal the
mean and the median differ, so you must say which one the scenario
value is:

| `scenario_value_is` | X | mean of p | median of p |
|---|---|---|---|
| `"median"` | exp(&sigma;&#8343;&#8345; z) | p&#8320; exp(&sigma;&#8343;&#8345;&sup2;/2) | p&#8320; |
| `"mean"` | exp(&sigma;&#8343;&#8345; z &minus; &sigma;&#8343;&#8345;&sup2;/2) | p&#8320; | p&#8320; exp(&minus;&sigma;&#8343;&#8345;&sup2;/2) |

Which one is right depends on where p&#8320; comes from. An
**estimate** (the k fitted by `spody calibrate`, a least-squares Cd)
is a mean: use `"mean"`. A **typical or catalogue value** ("Cd = 2.2,
within a factor 1.3") is a median: use `"median"` and give
`sigma_ln = ln 1.3`. The choice matters: with 30 % the two differ by
4.4 % in the mean of p, and in LEO the along-track error grows
linearly with the drag factor, so that 4.4 % becomes an along-track
bias of the cloud. There is no default on purpose.

### `[montecarlo.process_noise]`: errors that change along the way

The two tables above disperse what is uncertain **at the start**: the
state, and parameters that keep their drawn value for the whole run.
Some errors are not like that. The real thermosphere departs from
NRLMSISE-00 by 5&ndash;15 % over a few hours and drifts again the next
day; a constant `density_scale`, however wide, cannot follow it, and
the cloud grows slower than the real error. Process noise lets the
error **change along the trajectory**, a different history in every
case.

Three entries, `density`, `acceleration` and `acceleration_1rev`,
each optional:

```toml
[montecarlo.process_noise]
density           = { sigma_ln = 0.05, tau_s = 345600.0, interval_s = 3600.0, scenario_value_is = "mean" }
acceleration      = { sigma_m_s2 = [9.4e-8, 0.0, 7.4e-8], tau_s = [300.0, 300.0, 3600.0], interval_s = 30.0 }
acceleration_1rev = { sigma_m_s2 = [3.2e-8, 8.0e-9, 2.1e-8], tau_s = 345600.0, interval_s = 600.0 }
```

#### `density`

| Key | Meaning |
|---|---|
| `sigma_ln` | Standard deviation of ln(&rho;<sub>case</sub>/&rho;<sub>model</sub>) at any instant (0.08 &asymp; 8 %). |
| `tau_s` | Correlation time [s]: two instants `tau_s` apart are correlated by e<sup>&minus;1</sup> &asymp; 0.37. |
| `interval_s` | Spacing of the noise nodes [s]; at most `tau_s`, and `tau_s / 10` or less recommended (the log notes a larger one). |
| `scenario_value_is` | `"median"` or `"mean"`, as for a lognormal parameter: is the scenario's density the median or the mean of the noisy one? Required. |
| `ap_doubling` | Optional, &gt; 0. Makes the noise follow the geomagnetic activity: the standard deviation becomes `sigma_ln` &times; (1 + Ap(t) / `ap_doubling`), i.e. `ap_doubling` is the Ap at which it doubles (below). Omitted: constant `sigma_ln`. |

The first four keys are required, and the scenario must have drag on.

**What each case does.** Case c multiplies the density used by the drag
force by exp(&eta;(t)), with &eta; a first-order Gauss-Markov process
(Ornstein-Uhlenbeck): stationary standard deviation `sigma_ln`,
autocorrelation e<sup>&minus;|&Delta;t|/tau_s</sup>. &eta; is computed
**exactly** at nodes every `interval_s` from the start (Gillespie
1996) and interpolated linearly between them, so the force stays
continuous and the integrator needs no extra stops. The factor
multiplies whatever calibration the case has (`density_scale`,
dispersed or not, or the `density_scale_file` k(t)). With `"mean"`
the factor is exp(&eta; &minus; `sigma_ln`&sup2;/2), whose mean is 1.

The nominal (case 0) has no noise. The noise reads its own random
streams, separate from those of the initial state and the parameters:
adding it leaves every drawn initial state and parameter, and the
nominal, exactly as they were, so a run with and one without the
noise can be compared case by case. The samples file does not list
the noise.

**Choosing the values.** `sigma_ln` and `tau_s` describe how the real
density departs from the model, which depends on the orbit, the solar
activity and the model. Measured on GRACE-FO (about 490 km, January
2024) from orbit fits on 3-hour arcs: `sigma_ln` &asymp; 0.08, `tau_s`
&asymp; 6&ndash;9 h. With those values and a 1000-case run, the
in-track sigma at 24 h is 96 m. A slow drift over days, which a
process with a correlation time of hours does not describe, comes on
top.

**Checked against theory.** For the noise alone, the in-track sigma
of the Monte Carlo agrees with the linear prediction
&sigma;<sub>I</sub>(t)&sup2; = &sigma;&sup2; &int;&int; g(t&minus;u)
g(t&minus;v) e<sup>&minus;|u&minus;v|/&tau;</sup> du dv, g the
measured in-track response to a density step, within 2&ndash;4 % at 6,
12 and 24 h (sampling error 2.2 %); halving `interval_s` changes the
24 h sigma by 0.04 %.

**Following the geomagnetic activity (`ap_doubling`).** The density
error of an empirical model is not the same every day: it is small
when the magnetosphere is quiet and several times larger in a storm.
With `ap_doubling` the standard deviation of node j becomes

&sigma;<sub>j</sub> = `sigma_ln` &times; (1 + Ap<sub>j</sub> / `ap_doubling`)

with Ap<sub>j</sub> the 3-hourly Ap of the bin holding the node, read
from the scenario's `space_weather_file`: observed values in the past,
the file's own forecast rows (CelesTrak `PRD`, about 45 days) in the
future. `sigma_ln` becomes the value with no activity, and
`ap_doubling` the Ap at which it doubles. This is the activity-scaled
density error of Wright (AGI, "Real-time estimation of local
atmospheric density"). The variance follows a change of activity with
time constant `tau_s` / 2, not at once. Kp to Ap for orientation:

| Kp | 2 | 3 | 4 | 5 (G1) | 6 (G2) | 7 (G3) | 8 (G4) | 9 (G5) |
|---|---|---|---|---|---|---|---|---|
| Ap | 7 | 15 | 27 | 48 | 80 | 140 | 240 | 400 |

Values measured on GRACE-FO C (about 480 km; February&ndash;May 2024,
storms of 24 March and 10 May) by a maximum-likelihood fit of the
density-scale history on 3-hour arcs: `sigma_ln` = 0.063,
`ap_doubling` = 64, `tau_s` &asymp; 15 h (54 600 s), plus a slow
component of 17 % over about 11 days that belongs in the initial
uncertainty of `density_scale`, not here. With them &sigma; is 7 % on a
quiet day (Ap 7), 13 % at Ap 64 and 36 % in a G4 storm (Ap 300).
Against a constant &sigma; the likelihood of the measured history
improves strongly (the normalised errors keep variance 1 from quiet
days to storms instead of 0.7 to 8.9).

What it buys, and what not, measured on October 2024 (not used to
size it; G4 storm on the 10th; 51 starts, orbit determination of
catalog quality): beyond 24 h some density noise in the forecast is
necessary (without it 14&ndash;16 % of the starts fall outside the 95 %
region at 48&ndash;72 h, against 5 %); with it about 8&ndash;10 %.
`ap_doubling` and a constant &sigma; chosen from the Ap at the start
give nearly the same result on that month. The starts whose forecast
runs into the storm stay partly outside: the storm shifts the density
systematically, which no zero-mean noise can foresee. Up to 24 h the
noise matters little: the uncertainty of `density_scale` at the start
dominates.

**Checked against theory with `ap_doubling`.** In the G5 storm of
10 May 2024 (Ap 7 to 400 within the day) the in-track sigma of the
noise-only Monte Carlo agrees with the linear prediction within
2&ndash;4 % at 6, 12 and 24 h (399 m against 390 m at 24 h). The
prediction must use the in-track response to a density change *at
each time*: in a storm the density, and with it the response, grows
during the day, and a single step response measured at the start
underestimates the 24 h sigma by 30 %.

#### `acceleration`

An acceleration in each case's own radial / in-track / cross-track
axes, standing for the forces the model leaves out (ocean tides,
thermal and attitude-dependent effects, the box-wing shape of a real
spacecraft). Each axis is its own Gauss-Markov process.

| Key | Meaning |
|---|---|
| `sigma_m_s2` | Three numbers &ge; 0: stationary standard deviation of the R, I, C acceleration [m/s&sup2;]. An axis with 0 stays exact; at least one must be &gt; 0. |
| `tau_s` | Correlation time [s]: one number for the three axes, or `[R, I, C]`. |
| `interval_s` | Spacing of the nodes [s], at most the shortest `tau_s` of an axis with sigma &gt; 0 (a tenth of it or less recommended). |

The values are computed exactly at nodes every `interval_s` and
interpolated linearly in time; the force rotates them into the RIC
axes of the case's current state (rotation only, the CCSDS RTN
convention), so a cross-track noise stays cross-track along the
orbit. It reads its own random streams (three, one per axis), separate
from the density noise and from the once-per-case draws: the drawn
states, parameters and density histories stay the same when it is
added. The nominal has no noise.

**Choosing the values.** A noise in an axis grows the error in that
axis and, through the orbital dynamics, in the coupled one (a radial
or in-track acceleration ends up mostly in-track). Sized from orbit
fits (manual values; they depend on the orbit and the model):
GRACE-FO cross-track &asymp; 1.6e-7 m/s&sup2;, LAGEOS-2 cross-track
&asymp; 1.3e-8 m/s&sup2;, both with `tau_s` &asymp; 30 min. In a
low orbit the in-track error is usually the density's: use the
`density` entry for it rather than an in-track acceleration.

**Checked against theory.** Two-body orbit at 490 km, noise on all
three axes (2, 1, 3 &times; 10&#8315;&#8311; m/s&sup2;, 30 min), 1000
cases: the R, I, C sigmas of the Monte Carlo agree with the linear
Clohessy-Wiltshire covariance driven by the same process (Van Loan
discretisation) within 1&ndash;3 % in-track and cross-track and within
1&ndash;9 % radially at 1, 6, 12 and 24 h (sampling error 2.2 %);
halving `interval_s` moves them within the sampling error.

#### `acceleration_1rev`

The same keys as `acceleration`, for the **once-per-revolution** part:
on each axis

&nbsp;&nbsp;&nbsp;&nbsp;a<sub>k</sub> = A<sub>k</sub> cos u + B<sub>k</sub> sin u,

u the argument of latitude of the case's current state (angle in the
orbit plane from the ascending node on the ICRF equator), A<sub>k</sub>
and B<sub>k</sub> two independent Gauss-Markov processes with the
`sigma_m_s2` and `tau_s` of axis k. A force that repeats once per
orbit &mdash; thermospheric winds, unmodelled tides, a radiation
pressure tied to the orbit geometry &mdash; drives the cross-track
motion at its own frequency, so the cross-track error grows
**linearly** in time; a noise constant in RIC only makes it grow like
&radic;t and cannot follow it. These are the once-per-revolution terms
of empirical orbit models (Beutler et al. 1994; J&auml;ggi et al.
2006). Its random streams are separate from every other noise.

**When to use it.** Measured on GRACE-FO (February 2024, orbit fits on
10 days): the cross-track error grew from 0.31 m at 6 h to 0.83 m at
24 h, nearly linearly; with the constant RIC acceleration alone the
calibrated cloud fell short at 24 h by a factor 2.2 in cross-track
variance, with the once-per-revolution terms added the
predicted/observed variance stayed within 0.8&ndash;1.25 on every axis
from 1 to 24 h.

**Checked against theory.** Noise-only Monte Carlo, two-body orbit at
490 km, sigma 1, 0.5, 2 &times; 10&#8315;&#8311; m/s&sup2; on R, I, C,
tau 6 h, 1000 cases: R, I, C sigmas agree with the linear
Clohessy-Wiltshire variance Var x(T) = h&#7488;Kh, K<sub>ij</sub> =
e<sup>&minus;|t<sub>i</sub>&minus;t<sub>j</sub>|/&tau;</sup> cos
n(t<sub>i</sub>&minus;t<sub>j</sub>), h the CW impulse response, within
1&ndash;5 % at 1, 6, 12 and 24 h.

## What a run does

1. **Checks** the uncertainty file and the scenario together (all the
   rules above).
2. **Creates the run folder and the log**, copies the scenario
   (`<ts>_input.toml`) and the uncertainty file (`<ts>_<name>.uq.toml`,
   its `scenario` line rewritten to the copy, the original kept as a
   comment): the folder is self-contained, and rerunning its
   `.uq.toml` gives the same cases.
3. **Draws every case** and writes them to `<ts>_<name>_samples.uq.csv`
   (see below). With `--samples-only` the command stops here.
4. **Keeps from the scenario only its dynamics.** Output files
   (`csv_file`, `bin_file`, `accelerations_file`, `events_log`) and
   every event (eclipse, altitude crossings, including stop-class ones)
   are ignored, and the log names what was ignored. Only an impact,
   which is always on, can end a case. The time span is the
   scenario's. For the ignored outputs, run `spody propagate` on the
   copied `<ts>_input.toml`.
5. **Checks every case before any runs** (its final values and run
   window, as `spody batch` does). A case that could not run would bias
   the statistics, so the command stops and lists them instead of
   skipping them.
6. **Propagates the nominal**, case 0: the scenario exactly as it is.
   Its trajectory is bit-identical to `spody propagate` on the same
   scenario.
7. **Propagates the N cases** in parallel, in chunks, keeping each
   trajectory in memory, and adds them to the statistics **in case
   order** &mdash; so every output is identical whatever
   `thread_number`. A case whose integration fails (not an impact: a
   numerical failure) stops the command.
8. **Writes the statistics** and closes the log with the impact
   count.

### Random numbers and reproducibility

The random numbers come from Philox4x64-10 (Salmon et al., SC'11), a
counter-based generator: number k of case c is a fixed function of
(`seed`, c, k). So case 37 is the same whether it runs first or last,
on one thread or on eight, and adding cases (raising `samples`) keeps
the first ones unchanged. Each uncertain quantity has its own
independent stream: the initial state one, each parameter another
(derived from its target path), the process noise others again, in a
separate family of streams that can never coincide with the first
two. Uniform numbers are turned into normal ones by the inverse
normal CDF (Wichura's AS241, accurate to about 1e-16).

### The samples file

`<ts>_<name>_samples.uq.csv` has one row per case, case 0 (the
nominal, no draw) included: the id, the standard normal numbers drawn
(`z_*` columns), the initial-state offsets in ICRF (`dx_km` ...
`dvz_kms`) and the parameter values. It is a valid **batch cases
file**: the log prints the `[batch.columns]` block that reruns these
exact cases with `spody batch` (offsets as `delta` columns, parameters
as overrides, `z_*` columns as metadata). The rerun reproduces every
Monte Carlo case bit for bit.

## The statistics

At every output epoch t of the nominal, with x&#8345;(t) the nominal
state and x&#7522;(t) the state of case i, SpOdy works with the
deviations d&#7522; = x&#7522; &minus; x&#8345; and computes, over
the n cases that have a state at t:

- the **bias** b = mean of d&#7522;: how far the centre of the cloud is
  from the nominal;
- the **covariance about the mean** C = &Sigma;(d&#7522; &minus;
  b)(d&#7522; &minus; b)&#7488; / (n &minus; 1): the shape and size of
  the cloud;
- the **second moment about the nominal** M = C (n &minus; 1)/n +
  b b&#7488;: the error of someone who uses the nominal as "the"
  orbit. It is not stored; it follows exactly from b and C.

In the linear regime b is close to zero and C &asymp; M. A growing b
is the sign that the dynamics is non-linear for this spread: the
cloud's centre drifts away from the nominal.

The sums run with Welford's update, which keeps full precision
however many cases are added.

### RIC and curvilinear coordinates

The ICRF numbers are rotated into the RIC axes **of the nominal at
each epoch** (rotation only). Along the orbit, however, a cloud
stretches into an arc, and straight RIC axes then report a false
radial error: a case 50 km of arc ahead at the same altitude of a
6878 km orbit is &minus;0.18 km "radial" in RIC; at 500 km of arc,
&minus;18 km. So SpOdy also gives the position in **curvilinear**
coordinates (Vallado & Alfano), with r&#8345; = \|r&#8345;\| and
r&#770;, &icirc;, c&#770; the nominal's RIC axes:

- radial: \|r\| &minus; r&#8345;
- in-track: r&#8345; atan2(r&middot;&icirc;, r&middot;r&#770;), the arc
  in the nominal's plane
- cross-track: r&#8345; asin(r&middot;c&#770; / \|r\|), the arc out of
  the plane

For the case above they read 0 and 50 km.

### Impacts

A case that impacts stops counting at its impact: n(t) is the number
of cases still flying at t, and from that moment the statistics
describe the survivors. If the nominal impacts, the statistics stop
there (its last record is its impact, where n = 0).

The impacts are in `<ts>_<name>_events.uq.bin` (one file for all
cases: each impact with its case number and state, plus the start and
end markers of every case), which the Analysis tab opens like a batch
events file. The log closes with how many cases impacted and **how far
that count can be trusted**. With N cases the observed fraction k/N
is only an estimate of the true impact probability, like a poll: a
different seed gives a different k. The line gives the 95 % interval
of the true probability:

| N | impacts k | log line |
|---|---|---|
| 100 | 4 | `4 of 100 cases (4.00 %), 95 % interval 1.57 % .. 9.84 % (Wilson)` |
| 1000 | 37 | `37 of 1000 cases (3.70 %), 95 % interval 2.70 % .. 5.06 % (Wilson)` |
| 1000 | 0 | `0 of 1000 cases, probability < 0.30 % at 95 %` |

For 0 < k < N it is Wilson's interval (Wilson 1927; recommended over
the textbook k/N &plusmn; 1.96&radic;(...) by Brown, Cai & DasGupta
2001, which fails exactly when impacts are rare: it gives "0 %, no
uncertainty" for k = 0). With no impact the only honest statement is
an upper bound, 1 &minus; 0.05^(1/N) &asymp; 3/N (the "rule of three",
Hanley & Lippman-Hand 1983): 1000 cases without an impact only show
that the probability is below 0.3 %. With every case impacting, the
mirror bound is given.

### How many cases

The uncertainty of every Monte Carlo number shrinks as 1/&radic;N:
four times the cases halve it. Two rules of thumb: a &sigma; estimated
from N cases has a relative error of about 1/&radic;(2(N &minus; 1))
(2.2 % at N = 1000); and showing that an event has a probability
below P needs at least 3/P cases without it.

## Output files

All in the run folder, all prefixed by the run's timestamp, all marked
`.uq` before the extension:

| File | Contents |
|---|---|
| `<ts>_input.toml` | the scenario as it was run |
| `<ts>_<name>.uq.toml` | the uncertainty file, pointing at the copy above |
| `<ts>_<name>.uq.log` | everything printed, refusals included |
| `<ts>_<name>_samples.uq.csv` | the drawn cases (a batch cases file) |
| `<ts>_<name>_nominal.uq.bin` | case 0 trajectory, `SPDYOUT_` |
| `<ts>_<name>_moments.uq.bin` | statistics at every nominal epoch, `SPDYUQM_` |
| `<ts>_<name>_clouds.uq.bin` | every case at the snapshots, `SPDYUQC_` (only with `snapshots_s`) |
| `<ts>_<name>_sigma.uq.csv` | the statistics in RIC, readable |
| `<ts>_<name>_events.uq.bin` | impacts and life markers, `SPDYEVTB` |
| `<ts>_<name>_case<i>.uq.bin` | each case's trajectory, `SPDYOUT_` (only with `case_outputs = true`; i zero-padded to N's digits) |

### `SPDYUQM_` &mdash; moments

Standard 24-byte header; payload = record size (352 bytes); first
reserved word = N. One record of 44 little-endian doubles per output
epoch of the nominal:

| Index | Field | Units |
|---|---|---|
| 0 | t (same label as the nominal's `SPDYOUT_`) | s |
| 1 | n, cases with a state at t | &mdash; |
| 2&ndash;7 | nominal state x&#8345;, ICRF | km, km/s |
| 8&ndash;13 | bias b, ICRF | km, km/s |
| 14&ndash;34 | C, ICRF, lower triangle by rows: C&#8321;&#8321;, C&#8322;&#8321;, C&#8322;&#8322;, C&#8323;&#8321;, ... (the order of the CCSDS OEM COVARIANCE block) | km&sup2;, km&sup2;/s, km&sup2;/s&sup2; |
| 35&ndash;37 | curvilinear position bias (radial, in-track, cross-track) | km |
| 38&ndash;43 | curvilinear position covariance, lower triangle | km&sup2; |

Fields that are undefined are NaN: everything after the nominal state
when n = 0, the covariances when n = 1.

### `SPDYUQC_` &mdash; clouds

Payload = 64 bytes; reserved words = N and the number of snapshots.
One record of 8 doubles per case with a state at a snapshot: t, case
number, d&#7522; (6, ICRF). Ordered by snapshot, then by case.

### `<name>_sigma.uq.csv`

One row per epoch: `t_s, n`; &sigma; in R, I, C and in the three
velocity components (from C rotated into the nominal's RIC); the
position correlations RI, RC, IC; the bias in R, I, C; the RMS about
the nominal in R, I, C (from M); the curvilinear &sigma; and bias.
Empty fields where n < 2.

### Reading them in Python

```python
from spody_io import read_uq_moments, read_uq_clouds, read_trajectory
from spody_io.uq import lower_to_full

mom, n_cases = read_uq_moments("…_iss_mc_moments.uq.bin")
C = lower_to_full(mom["cov"], 6)          # (epochs, 6, 6)
cloud, n_cases, n_snap = read_uq_clouds("…_iss_mc_clouds.uq.bin")
```

## The Uncertainty tab

The GUI edits and runs uncertainty files in its own tab (layout and
buttons in chapter 4). The Run tab does not list `*.uq.toml` files,
and **File &rsaquo; Open** of one brings the Uncertainty tab to the
front. The bundled example `examples/iss_montecarlo/` (the ISS for
3 days, 500 cases, about a minute on 8 threads) is a ready-made
starting point.

The form mirrors the file, one group per table, with the keys as
labels:

- **`[montecarlo]`**: `scenario` lists the scenario TOMLs under the
  working dir (run-folder copies and drafts left out), plus
  **Browse&hellip;**. Under it a grey line reads the scenario back:
  object kind, duration, and the output step, since every snapshot
  must be a multiple of it. `snapshots_s` takes seconds separated by
  commas.
- **`[montecarlo.initial_state]`** (a check box turns it off): `axes`
  RIC or ICRF; *given as* standard deviations (with an optional 6&times;6
  correlation) or a 6&times;6 covariance. Both matrices are symmetric
  by construction: typing a cell fills its mirror, and the
  correlation's diagonal is fixed at 1.
- **`[montecarlo.parameters]`**: one row per parameter. *Target*
  lists only what can be dispersed for the scenario's object
  (spacecraft or debris); *Scenario value* shows the value the
  scenario gives it (the centre of the distribution); *&sigma; given
  as* offers the kinds the distribution accepts (absolute or percent
  for a normal, percent or &sigma; of ln for a lognormal);
  *Scenario value is* (mean or median) is enabled only for a
  lognormal and has no default.

The TOML preview under the form shows what Save writes. While the
form cannot be written yet (no scenario, a lognormal without *Scenario
value is*, a number that does not parse) the preview lists the
reasons and Save refuses with the same list. Everything else is
checked by the engine: **Draw samples** runs the same checks as a
full run in a fraction of a second. Save keeps the comment block at
the top of a file; comments further down are not kept. A file inside
a run folder is the run's copy, so Save on it always asks for a new
path.

## Viewing the results

Load the moments file (`_moments.uq.bin`) or the clouds file
(`_clouds.uq.bin`) in the Analysis tab, or press **Open results in
Analysis** after a run. The views are listed in chapter 9:

- moments: &sigma; of position and velocity in R, I, C, curvilinear
  vs RIC &sigma;, the position correlations, the bias with its
  &plusmn;2 standard-error band, the RMS about the nominal, and n(t);
- clouds: the cases at each snapshot in the I&ndash;R, I&ndash;C and
  C&ndash;R planes with their 3&sigma; ellipse, the I&ndash;R plane in
  curvilinear coordinates, and **Cloud 3D**.

The Info tab gives, for the moments file, the epochs, the cases alive
at the start and at the end, the error of the &sigma; estimates
(1/&radic;(2(N &minus; 1))) and the last epoch's &sigma;, bias and RMS;
for the clouds file, the &sigma; in R, I, C at each snapshot. Both
also show the settings of the run's `.uq.toml`.

### Reading the 3D cloud

**Cloud 3D** is a scene of its own, with its own options bar
(*Coordinates* RIC or curvilinear, *Show* all snapshots or one,
*Point size*) and a **How to read this view** button that opens the
guide below.

Each point is one case at the chosen snapshot, placed in the R, I, C
axes of the nominal (case 0), which sits at the origin. In
curvilinear coordinates the in-track and cross-track components are
arc lengths along the nominal orbit instead of straight-line
distances.

The three components differ by orders of magnitude (in-track errors
grow to kilometres, radial and cross-track stay at tens of metres), so
**each axis is stretched by its own standard deviation**: one unit
along R, I or C is one &sigma; of the points shown, worth the number of
metres the legend gives. Without correlations the cloud would be a
round ball; an elongated or tilted cloud shows correlated errors.

The &sigma; are computed over **all the points on screen**. With all
snapshots shown they come from the clouds together, dominated by the
latest and widest one, so the earlier clouds look small: that is the
scale, not an error. Show one snapshot to see it at its own scale.

The R, I, C arrows start at the nominal and are 3&sigma; long. The
cloud's centre is off the origin when the cases are biased (drag, for
instance, running them ahead of the nominal): that offset is the bias
of the *Centre and error* plots.

Each translucent shell is the **3&sigma; ellipsoid of one snapshot's
cloud**, centred on its mean and built from its full covariance,
correlations included. For a Gaussian cloud 2.9 % of the cases fall
outside it: in three dimensions 3&sigma; encloses 97.1 %, not the
99.7 % of one dimension. The legend counts the cases outside for each
snapshot. Many more than 2.9 % means the cloud is not Gaussian,
typically because the dynamics curl it into a banana along the orbit;
the curvilinear coordinates straighten that shape. In the ISS example
at 3 days, 29 of 500 cases are outside in RIC and 19 in curvilinear,
against about 15 for a Gaussian.

## Practical notes

- **Time.** A case costs about as much as one `spody propagate`; the
  run costs N + 1 of them, divided by the threads.
- **Memory.** The trajectories of one chunk (8 cases per thread, at
  most 512 MB) are in memory at once, plus the nominal and the
  statistics (about 0.5 kB per epoch).
- **Reentries.** A case that reaches the ground needs the integrator to
  follow a fast final descent. With a tight `rel_tol` (1e-11) the
  step there can fall below `integrator.h_min_s`; the case then fails
  numerically and stops the whole run. For reentry studies set
  `h_min_s` well below the default (1e-9 s works on a 200 km decay)
  or relax `rel_tol` to 1e-9.
- **Non-Gaussian clouds.** b and C describe a cloud completely only if
  it is Gaussian. A long arc stretched along the orbit is not; the
  curvilinear moments and the snapshot clouds are there to show it.

## References

- J. K. Salmon et al., *Parallel random numbers: as easy as 1, 2, 3*,
  SC'11 (2011) &mdash; Philox.
- M. J. Wichura, *Algorithm AS 241: the percentage points of the
  normal distribution*, Applied Statistics 37 (1988) 477&ndash;484.
- G. E. Uhlenbeck, L. S. Ornstein, *On the theory of the Brownian
  motion*, Physical Review 36 (1930) 823&ndash;841.
- D. T. Gillespie, *Exact numerical simulation of the
  Ornstein-Uhlenbeck process and its integral*, Physical Review E 54
  (1996) 2084&ndash;2091.
- G. Beutler, E. Brockmann, W. Gurtner, U. Hugentobler, L. Mervart,
  M. Rothacher, A. Verdun, *Extended orbit modeling techniques at the
  CODE processing center of the International GPS Service for
  Geodynamics (IGS)*, Manuscripta Geodaetica 19 (1994) 367&ndash;386.
- A. J&auml;ggi, U. Hugentobler, G. Beutler, *Pseudo-stochastic orbit
  modeling techniques for low-Earth orbiters*, Journal of Geodesy 80
  (2006) 47&ndash;60.
- W. H. Clohessy, R. S. Wiltshire, *Terminal guidance system for
  satellite rendezvous*, Journal of the Aerospace Sciences 27 (1960)
  653&ndash;658.
- C. F. Van Loan, *Computing integrals involving the matrix
  exponential*, IEEE Transactions on Automatic Control 23 (1978)
  395&ndash;404.
- B. P. Welford, *Note on a method for calculating corrected sums of
  squares and products*, Technometrics 4 (1962) 419&ndash;420.
- D. A. Vallado, S. Alfano, *Curvilinear coordinate transformations
  for relative motion*, Celestial Mechanics and Dynamical Astronomy
  118 (2014) 253&ndash;271.
- E. B. Wilson, *Probable inference, the law of succession, and
  statistical inference*, JASA 22 (1927) 209&ndash;212.
- L. D. Brown, T. T. Cai, A. DasGupta, *Interval estimation for a
  binomial proportion*, Statistical Science 16 (2001) 101&ndash;133.
- J. A. Hanley, A. Lippman-Hand, *If nothing goes wrong, is
  everything all right?*, JAMA 249 (1983) 1743&ndash;1745.
