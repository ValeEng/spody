# Installation and first launch

SpOdy is distributed as a single archive containing everything it
needs to run. This chapter takes you from receiving that archive to
seeing your first propagation results on screen, including the
mandatory one-time setup wizard.

## Prerequisites

The minimum requirements are unsurprising for a desktop scientific
application:

- **Windows 10 (64-bit) or Windows 11**. The bundle is built for
  `x86_64`; ARM-based devices are not supported in this release.
- About **600 MB of free disk space** for the application itself,
  plus **roughly 30 MB to 350 MB** for the external data files the
  setup wizard downloads (the exact figure depends on which
  ephemeris coverage profile you choose &mdash; see chapter 3).
- A working **internet connection** the first time you run the
  application, so the setup wizard can fetch the planetary
  ephemeris and lunar gravity-model data from NASA's public
  servers. After the first run, SpOdy works fully offline.
- A graphics adapter that supports **OpenGL 3.2 or higher**. Any
  Windows-supported integrated GPU from the last decade is fine;
  the VTK-based 3D viewer falls back to software rendering when no
  hardware acceleration is available, with a small frame-rate cost.

You do **not** need Python installed: the bundle ships with its own
Python interpreter plus every library it depends on. You also do
not need administrator privileges to install or run SpOdy.

## Installing

Installation in the conventional sense does not exist: there is no
installer wizard, no Start menu entry created automatically, and no
registry keys written. SpOdy is fully portable.

1. Locate the archive you received. It is named in the form
   `spody-gui-<version>-win64.zip` (or `.7z` for the smaller
   compressed variant).
2. Right-click the archive and pick **Extract All...** &mdash; or
   use any archiver such as 7-Zip if you prefer.
3. Choose a destination folder. SpOdy stores all its data inside the
   extracted folder, so pick somewhere you have write access:
    - **Your user folder**, for example
      `C:\Users\<you>\Apps\spody-gui\`, is the safest default.
    - **An external drive** (USB stick, network share) works too if
      you want a portable installation you can carry between
      machines.
    - **`C:\Program Files\`** is *not* recommended: the wizard needs
      to write the downloaded data files into the same folder, and
      Windows restricts write access there to administrators.
4. Open the extracted folder and confirm you can see at least these
   four items:
    - `spody-gui.exe`
    - `spody.exe`
    - `data/` (empty at this stage)
    - `_internal/` (do not touch)

The bundle is now ready to launch.

### Verifying the bundle (optional)

If you want to make sure the archive transferred without corruption
before running anything, the distribution comes with a `SHA256SUMS`
text file listing the expected SHA-256 hash of each top-level file.
From a PowerShell prompt opened in the extraction folder:

```powershell
Get-FileHash spody-gui.exe -Algorithm SHA256
Get-FileHash spody.exe     -Algorithm SHA256
```

Compare the hexadecimal values against the matching lines in
`SHA256SUMS`. Mismatches mean the download was truncated or
tampered with &mdash; redownload from the original source.

## First launch

Double-click `spody-gui.exe`. After a brief warm-up the main window
appears, with two tabs at the top (**Run** and **Analysis**) and an
empty menu bar.

Because no data files are present yet, you will immediately see two
modal pop-ups:

1. An informational dialog announcing **"Setup needed"** with the
   path to the data folder it expects to populate (always
   `data/` relative to `spody-gui.exe`).
2. Following that, the **Setup wizard** itself.

The wizard is the topic of the next chapter. For now it is enough
to know that until you complete it, the application will refuse to
launch any propagation: a *hard run guard* checks that all required
data files are present before every run and will pop the wizard
back up if any are missing.

> The first launch can take 5 to 10 seconds before the window
> becomes responsive while the bundled Python interpreter and the Qt
> framework warm up. Subsequent launches are typically under a
> second &mdash; the operating system caches the bundle in memory.

## Settings file location

SpOdy persists a handful of user preferences (the path to `spody.exe`,
the data-folder override, the recent-files list, the 3D Moon texture
choice, the last-selected ephemeris coverage profile) through the
standard Windows registry under
`HKEY_CURRENT_USER\Software\SpOdy\SpOdy`.

Nothing inside the bundle folder is modified by writing settings,
so the bundle remains fully portable: you can copy or move the
folder to a different machine, and the application will start
fresh with the wizard on first launch there, ignoring whatever
settings remain in the old machine's registry.

To **reset SpOdy to a clean state** without uninstalling, delete the
registry key above (via `regedit`) and the `data/` folder next to
the executable. The next launch will behave exactly as if you had
just extracted the bundle.

## Uninstalling

Drag the extracted folder to the Recycle Bin. That is the entire
uninstallation procedure. There is nothing else to clean up except
the registry key mentioned above, which is harmless if left behind.
