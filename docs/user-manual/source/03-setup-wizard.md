# The setup wizard

A fresh SpOdy bundle cannot run a propagation. Two large data
files &mdash; a planetary ephemeris and a lunar gravity-model
&mdash; are needed before the first integration step, and neither
is shipped inside the archive (they are externally maintained,
publicly distributed, and together comprise hundreds of megabytes
that change rarely). The setup wizard is the dialog that downloads
those files into the bundle's `data/` folder, then converts the raw
ephemeris into the internal binary format the engine expects. You
run it once at first launch and never think about it again, unless
you choose later to widen the ephemeris coverage or upgrade to a
different gravity model.

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

The assets fall into two categories:

**Raw assets** are files SpOdy fetches verbatim from public
servers:

- the JPL `DE440` planetary ephemeris (header + one or more ASCII
  chunks); the wizard offers two coverage profiles, see section
  3.2;
- the GRGM1200B lunar harmonic-gravity coefficients (the `.tab`
  file with the spherical-harmonic coefficients and a small `.lbl`
  metadata sidecar from the PDS Geosciences Node).

**Derived assets** are files SpOdy produces from raw ones on your
machine:

- `de440.spody`, the internal binary format the engine expects.
  Built once from the downloaded DE440 ASCII chunks by invoking the
  `spody.exe convert ephemeris` subcommand under the hood. The
  conversion runs **automatically** as soon as the raw inputs are
  complete; you never click a button for it.

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
  GRGM1200B `.tab` and `.lbl` files.

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

Once the wizard sees that every required *raw* DE440 file is
present and the derived `de440.spody` is either missing or older
than the most recent raw chunk, it triggers the conversion
automatically. You will see the **Conversion (auto)** status line
at the bottom of the wizard switch through three states in
sequence:

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
