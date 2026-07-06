# The setup wizard

A fresh SpOdy bundle cannot run a propagation. Several large data
files &mdash; a planetary ephemeris, a gravity-model coefficient
set, and (for Earth-centred runs) the IERS Earth-orientation
tables &mdash; are needed before the first integration step, and
none is shipped inside the archive (they are externally maintained,
publicly distributed, and together comprise hundreds of megabytes
that change rarely). The setup wizard is the dialog that downloads
those files into the bundle's `data/` folder, then converts the raw
ephemeris and gravity coefficients into the internal binary formats
the engine expects. You run it once at first launch and never think
about it again, unless you choose later to widen the ephemeris
coverage, upgrade to a different gravity model, or refresh the
Earth-orientation tables with a newer IERS bulletin.

## When the wizard appears

The wizard pops in three situations:

1. **At first launch**, when no data files are present in the
   bundle's `data/` folder.
2. **At any later launch**, when one or more required files are
   missing or corrupt (this can happen if you delete the `data/`
   folder by hand, or if the previous download was interrupted and
   left a half-written file).
3. **On demand**, when you choose **Settings &rsaquo; Setup
   wizard...** from the menu bar. You typically reach for this when
   you want to switch ephemeris coverage profiles, add an extra
   gravity model, or simply re-verify that everything is in order.

While the wizard is open, the rest of the application keeps running
in the background. You can close the wizard at any time with the
**Close** button at the bottom-right; nothing you have downloaded so
far is lost.

## What the wizard manages

The wizard is structured as a list of *assets*, one card per file
it needs to put on disk. Each card shows:

- a **status icon** indicating whether the file is present
  (`✓` green), absent (`✗` red), or present but too small to be
  trustworthy (`⚠` amber);
- the asset's **human name** and, if present, its on-disk size;
- the **download URL** in an editable field (more on this in
  section 3.4);
- a **progress bar** (mostly idle until you press Download);
- a **Download** button on the right that toggles to **Cancel** while
  a transfer is in flight.

The assets fall into three categories:

**Raw required assets** are files SpOdy fetches verbatim from public
servers and absolutely needs to run a propagation:

- the JPL `DE440` planetary ephemeris (header + one or more ASCII
  chunks); the wizard offers two coverage profiles, see section
  3.2;
- the GRGM1200B lunar harmonic-gravity coefficients (the `.tab`
  file with the spherical-harmonic coefficients and a small `.lbl`
  metadata sidecar from the PDS Geosciences Node);
- the EIGEN-6C4 Earth gravity-model `.gfc` file (ICGEM format,
  ~178 MB) used when `central_body = "Earth"`;
- the IERS Earth-orientation files (`finals2000A.all` from the IERS
  Rapid Service plus the three IAU 2006 X/Y/s+XY/2 series tables
  `tab5.2{a,b,d}.txt`) needed by the engine's `R_ICRF↔ITRS`
  rotation;
- the CelesTrak combined space-weather table (`SW-All.csv`, daily
  F10.7 + 3-hour Ap) feeding the NRLMSISE-00 drag density model,
  needed only for runs with `force_model.drag = true`. Its card
  shows the date of the last *observed* row after download. All of
  these are Earth-specific.

**Derived assets** are files SpOdy produces from raw ones on your
machine:

- `de440.spody`, the internal binary format the engine expects.
  Built once from the downloaded DE440 ASCII chunks by invoking the
  `spody.exe convert ephemeris` subcommand under the hood;
- `eigen-6c4.tab`, the GRGM-style coefficient table the engine reads
  for Earth harmonics, converted from `EIGEN-6C4.gfc` by the
  `spody.exe convert harmonics_icgem` subcommand. The resulting
  `.tab` is ~252 MB on disk (2.4 M coefficient rows up to degree
  2190).

Both derived assets are produced **automatically** as soon as the
raw inputs are complete; you never click a button for them. The
wizard streams the converter's progress (chunk id for DE440,
running `n = D / D_max` for the harmonics converter) into the
**Conversion (auto)** status line at the bottom of the dialog.

**Optional raw assets** are files SpOdy can use to enrich the
experience but does not require to run:

- the NASA SVS LROC color Moon texture (2K equirectangular TIFF,
  ~3 MB). When present, the Analysis tab renders the 3D Moon and
  the impact lat/lon backgrounds with the actual lunar surface;
  when absent, the views fall back to a flat-grey sphere;
- the NASA Visible Earth "Blue Marble" December 2004 texture
  (equirectangular JPEG, ~3 MB). Same role for Earth-centred
  scenes: when present, the 3D viewer renders Earth with the
  actual continents and oceans; when absent, the central-body
  sphere stays flat-grey. When the Moon appears as a *third
  body* in an Earth-centred scene, this is also the texture
  that paints its body-fixed marker so the Moon is still
  recognisable at its true ~384,000 km distance;
- the Solar System Scope Milky Way 8K star map
  (equirectangular JPEG, ~6 MB, CC BY 4.0). When present and the
  *Show starfield* toggle in the Scene options dialog is
  enabled, the 3D Analysis scene replaces the dark background
  with a real star map. The texture is re-projected on first use
  to align with the ICRF axes (catalogue stored in galactic
  coords; SpOdy chains a J2000 ICRF&rarr;galactic rotation before
  the lookup so the bulge ends up at ICRF -Y), with the rotated
  copy cached on disk for instant subsequent loads.

The three textures are asked for on demand by clicking **Download**
on their respective cards &mdash; the *Download all missing*
button intentionally leaves them alone so a fresh install does
not pay the download cost unless the user wants the textures.

Every asset carries internal metadata identifying its **central
body** (`Moon`, `Earth`, or body-agnostic for the DE440
ephemeris) and its **category** (`harmonics`, `ephemeris`,
`texture`, `eop`, `iau2006`, &hellip;). The TOML form's
dropdowns in `[force_model].harmonics_file`, `[force_model]
.eop_file`, `[force_model].iau2006_dir` and `[ephemeris].file`
use that metadata to show only the assets that apply to the
currently-selected `central_body` &mdash; the user never has to
type a path by hand. The Earth-only rows (`eop_file`,
`iau2006_dir`) appear in the form only when
`central_body = "Earth"`.

## Choosing an ephemeris coverage profile

The DE440 planetary ephemeris is split into ~100-year chunks. SpOdy
gives you two pre-set profiles, picked through a radio at the top
of the wizard:

| Profile          | Chunks                  | Coverage window | Download size |
|------------------|-------------------------|-----------------|---------------|
| **Modern era** *(default)* | 1 chunk (`ascp01950.440`)  | 1950 &ndash; 2050 | ~30 MB         |
| **Full pack**    | 11 chunks (`ascp01550..ascp02550`) | 1550 &ndash; 2650 | ~340 MB        |

The default is *Modern era*. It is the right pick for anyone running
near-present scenarios, which means almost everyone reading this
manual: any simulation epoch from 1950 to 2050 is covered exactly,
with the same accuracy as the full pack. The full pack only matters
for historical reconstructions (lunar landings of the late 1960s,
the Apollo program, deep-time analyses of orbital secular drift) or
far-future scenarios.

Switching coverage at any time is non-destructive: changing the
radio rebuilds the asset list to reflect the new requirement, but
nothing is deleted from disk. So if you start with *Modern era*,
later upgrade to *Full pack*, then change your mind, the extra
chunks remain on disk and the wizard simply stops requiring them.
The derived `de440.spody` always includes every chunk it finds in
the `DE440/` folder, regardless of profile, so you never lose
coverage by toggling.

> Storage for the *Full pack* profile is dominated by the eleven
> ASCII chunks (~30 MB each); the derived `de440.spody` from the
> full set is about 100 MB on disk.

## Downloading the data

Press **Download** on a single card to fetch that file. For a
fresh install the fast path is the **Download all missing** button
in the wizard's footer: it walks every card whose state is `✗` or
`⚠` and starts the download. Cards in flight show their progress
in the bar; you can cancel one without affecting the others by
clicking the **Cancel** button that replaces **Download** during a
transfer.

Failed downloads do not leave corrupted files behind: SpOdy writes
each transfer to a `.part` sidecar first and renames it to the
final name only after the HTTP response completes successfully.
If the connection drops mid-transfer, the next download attempt
starts over rather than appending to a stale partial.

### Why the URL field is editable

The URLs SpOdy ships with point at the canonical sources we have
verified work at the time of the release:

- JPL's FTP-over-HTTPS server for the DE440 ASCII chunks;
- the PDS Geosciences Node at Washington University for the
  GRGM1200B `.tab` and `.lbl` files;
- GFZ Potsdam's ICGEM service for the EIGEN-6C4 `.gfc` Earth
  gravity coefficients;
- the IERS Rapid Service / Prediction Centre for `finals2000A.all`
  and the IAU 2006 X/Y/s+XY/2 conventions tables.

Both servers are stable, public, and well-maintained, but URLs do
move over time. The **editable URL field** on each card lets you
paste a different location if a download fails &mdash; for instance,
if NASA mirrors the file under a different path after a server
reorganisation, or if your organisation hosts an internal mirror
behind a firewall. The wizard does not persist URL overrides: they
apply only to the current dialog session. Once you confirm a new
URL works, please share it with the SpOdy maintainers so the next
release ships with the corrected default.

## Conversion to the internal format

Two derived assets need a conversion step. The wizard runs both
automatically, sharing the **Conversion (auto)** status line at
the bottom of the dialog.

### `de440.spody` (planetary ephemeris)

Once the wizard sees that every required *raw* DE440 file is
present and the derived `de440.spody` is either missing or older
than the most recent raw chunk, it triggers the conversion
automatically. The status line cycles through three states:

1. *waiting on raw DE440 (&hellip;)* &mdash; while downloads are still in
   flight;
2. *converting&hellip; 01950* &mdash; the engine is reading the ASCII
   chunks (the chunk IDs are listed live);
3. *de440.spody ready (98.4 MB)* &mdash; success.

The conversion itself takes a few seconds for the *Modern era*
profile and a few tens of seconds for the *Full pack*. It uses the
`spody.exe convert ephemeris` subcommand under the hood; see
chapter 12 if you want to run the same conversion manually from a
shell.

### `eigen-6c4.tab` (Earth harmonics)

When the EIGEN-6C4 `.gfc` is downloaded, the wizard auto-runs the
ICGEM&rarr;tab converter the same way:

1. *waiting on raw EIGEN-6C4 (&hellip;)* &mdash; while the download
   is in flight;
2. *converting harmonics&hellip; n = 215 / 2190* &mdash; progress
   ticks per 100 degrees of the spherical-harmonic series;
3. *eigen-6c4.tab ready (252 MB)* &mdash; success.

Because the raw `.gfc` is 178 MB and the converter walks the full
2190-degree series row by row, this conversion takes a couple of
minutes on a typical laptop. It uses
`spody.exe convert harmonics_icgem` under the hood (chapter 12).

The two conversions run sequentially when both DE440 and EIGEN-6C4
are downloaded at the same time; the status line carries the
currently-active conversion's progress.

## Daily-table freshness checks (EOP + space weather)

Two of the wizard's assets are living tables that their upstreams
keep regenerating:

- `finals2000A.all` is rebuilt by IERS every Thursday. Old copies
  still work for past dates, but the *predicted* portion (covering
  the next ~365 days from the file's vintage) drifts; for runs
  whose epoch is more than a few weeks past your local copy's
  vintage, the predicted UT1-UTC and polar-motion values are no
  longer the best estimates IERS can offer.
- `SW-All.csv` (the CelesTrak space-weather table feeding the drag
  density model, *Space weather* card) is regenerated **daily**:
  yesterday's F10.7 / Ap observations only appear in a copy
  downloaded today, and the ~45-day prediction tail ages just as
  fast. A stale copy makes the engine either refuse a
  near-present drag run (window past the predicted horizon) or
  silently use older predictions where observations now exist.

On every launch SpOdy issues one lightweight HTTP HEAD request per
table (only for the ones already on disk) and compares the
server's `Last-Modified` header against the local file's mtime
(and the `Content-Length` against the local file's size). If the
server's copy is newer, a non-blocking pop-up appears:

> A newer finals2000A.all / SW-All.csv is available on the
> server. Download the latest now?

Clicking **Yes** opens the wizard and triggers that card's
download. The freshness check itself is silent on success and on
transient network failure (no connectivity at launch is not an
error). The GUI does not refresh the in-memory rotation provider
mid-session either &mdash; it stat()s the file on every rotation
call, so a fresh download from the open wizard takes effect on the
next 3D plot redraw without restarting the app. The engine reads
the space-weather file at run start, so a refreshed download is
picked up by the next run automatically.

## When everything is green

A complete bundle has every card showing a green `✓` and a status
line reading *de440.spody ready* with a size in megabytes. At that
point you can close the wizard with the **Close** button and start
using the application: opening one of the example TOML files, or
building a new scenario from scratch in the Run tab.

## Re-opening the wizard later

The wizard remains available at any time through **Settings
&rsaquo; Setup wizard...** in the menu bar. The two most common
reasons to reopen it later are:

- **Widening coverage**: you started with *Modern era* but now want
  to run an Apollo-era scenario, so you flip to *Full pack* and
  click **Download all missing** to fetch the additional historical
  chunks.
- **Re-verifying after a copy**: you moved the bundle to a new
  machine and want a quick visual confirmation that nothing got
  lost along the way. The wizard's status icons answer that
  instantly.

You can also open the wizard purely to **change the data folder**
through the **Change&hellip;** button at the top. SpOdy persists
the override in the registry and uses it from then on; the bundle's
`data/` folder is no longer touched. This is useful if you keep
multiple SpOdy bundles around but want them to share a single
copy of the (potentially hundreds of megabytes of) downloaded data.
