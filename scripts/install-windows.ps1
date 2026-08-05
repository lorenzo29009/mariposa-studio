<#
  Mariposa Studio - Windows installer (PowerShell).

  Driven by install-windows.bat. Sets up EVERYTHING with no winget dependency:
    - Python 3.12 (detected via PATH / py launcher / registry / common dirs;
      downloaded from python.org and installed silently if missing)
    - ffmpeg (winget if available, else a static build downloaded + put on PATH)
    - eSpeak NG (winget; the Script Animator measures clip lengths with it)
    - the app venv (PySide6 + opencv)
    - WhisperX in %USERPROFILE%\whisperx  (required; ~3 GB)
    - the Gemini API key
    - a Desktop + Start-Menu shortcut with the app icon
  then launches the app.

  PowerShell (not .bat) because detection, downloads, free-space checks, PATH
  refresh and shortcut creation are all far more reliable here.
#>

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent      # scripts\ -> repo root
Set-Location $root

function Section($t) { Write-Host ""; Write-Host ("=" * 52); Write-Host " $t"; Write-Host ("=" * 52) }
function Info($t)    { Write-Host ">> $t" }

Section "Mariposa Studio - Installer for Windows"
Write-Host "Sets up Python, ffmpeg, eSpeak NG, the app, WhisperX (German"
Write-Host "Captions) and your Gemini key - then opens the app."
Write-Host "Takes ~10-15 min (WhisperX)."
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Find a Python 3.10-3.12 (WhisperX has no wheels for 3.13+); install 3.12
#    from python.org if none is found. Returns the python.exe path, or $null.
# ---------------------------------------------------------------------------
function Test-Py($exe) {
    try {
        $v = & $exe -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(10|11|12)$') { return $true }
    } catch {}
    return $false
}

function Find-Python {
    # a) py launcher
    foreach ($v in '3.12','3.11','3.10') {
        try {
            $p = & py "-$v" -c "import sys;print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p) -and (Test-Py $p)) { return $p }
        } catch {}
    }
    # b) python / python3 on PATH
    foreach ($name in 'python','python3') {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c -and (Test-Py $c.Source)) { return $c.Source }
    }
    # c) common install dirs
    $dirs = @()
    foreach ($d in 'Python312','Python311','Python310') {
        $dirs += (Join-Path $env:LOCALAPPDATA "Programs\Python\$d\python.exe")
        $dirs += (Join-Path $env:ProgramFiles "$d\python.exe")
        $dirs += (Join-Path ${env:ProgramFiles(x86)} "$d\python.exe")
    }
    foreach ($p in $dirs) { if ((Test-Path $p) -and (Test-Py $p)) { return $p } }
    # d) registry (authoritative for python.org installs)
    foreach ($hive in 'HKLM:','HKCU:') {
        foreach ($node in 'SOFTWARE\Python\PythonCore','SOFTWARE\WOW6432Node\Python\PythonCore') {
            foreach ($ver in '3.12','3.11','3.10') {
                $key = "$hive\$node\$ver\InstallPath"
                try {
                    $ip = (Get-ItemProperty -Path $key -ErrorAction Stop).'(default)'
                    if ($ip) {
                        $p = Join-Path $ip 'python.exe'
                        if ((Test-Path $p) -and (Test-Py $p)) { return $p }
                    }
                } catch {}
            }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Info "No suitable Python found - downloading Python 3.12 from python.org..."
    $url = 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe'
    $exe = Join-Path $env:TEMP 'python-3.12.8-amd64.exe'
    try {
        Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
        Info "Installing Python (silent, adds to PATH)..."
        Start-Process -FilePath $exe -ArgumentList @(
            '/quiet','InstallAllUsers=0','PrependPath=1','Include_launcher=1','Include_test=0'
        ) -Wait
    } catch {
        Write-Host "X Could not download/install Python automatically: $($_.Exception.Message)"
        Write-Host "  Install Python 3.12 from https://python.org (tick 'Add to PATH'), then re-run."
        Read-Host "Press Enter to close"; exit 1
    }
    $py = Find-Python
    if (-not $py) {
        Write-Host "X Python was installed but still not detected. Close this window and re-run."
        Read-Host "Press Enter to close"; exit 1
    }
}
Info "Using Python: $py  ($(& $py --version 2>&1))"

# ---------------------------------------------------------------------------
# 2. ffmpeg - on PATH for Flow Cropper + Captions. winget if present, else a
#    static build downloaded into %LOCALAPPDATA%\Mariposa\ffmpeg and put on PATH.
# ---------------------------------------------------------------------------
function Has-Ffmpeg { return [bool](Get-Command ffmpeg -ErrorAction SilentlyContinue) }

if (-not (Has-Ffmpeg)) {
    Info "ffmpeg not found - installing..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        try { & winget install --silent --source winget --accept-package-agreements --accept-source-agreements Gyan.FFmpeg } catch {}
    }
    if (-not (Has-Ffmpeg)) {
        try {
            $dest = Join-Path $env:LOCALAPPDATA 'Mariposa\ffmpeg'
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            $zip = Join-Path $env:TEMP 'ffmpeg.zip'
            Info "Downloading a static ffmpeg build..."
            Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $zip -UseBasicParsing
            Expand-Archive -Path $zip -DestinationPath $dest -Force
            $bin = Get-ChildItem -Path $dest -Recurse -Filter ffmpeg.exe | Select-Object -First 1
            if ($bin) {
                $bindir = Split-Path $bin.FullName -Parent
                # Persist on the USER PATH (future launches) + this session.
                $userPath = [Environment]::GetEnvironmentVariable('Path','User')
                if ($userPath -notlike "*$bindir*") {
                    [Environment]::SetEnvironmentVariable('Path', "$userPath;$bindir", 'User')
                }
                $env:Path = "$env:Path;$bindir"
                Info "ffmpeg installed to $bindir"
            }
        } catch {
            Write-Host "  ! Could not install ffmpeg automatically. Flow Cropper / Captions"
            Write-Host "    need it - get a build from https://www.gyan.dev/ffmpeg/builds/."
        }
    }
} else {
    Info "ffmpeg already on PATH."
}

# ---------------------------------------------------------------------------
# 2b. eSpeak NG - the Script Animator MEASURES how long each line takes to say
#     by rendering it and reading the audio length, instead of predicting it from
#     the text (which ran 16% fast and shipped a 12.4s clip in a 10s slot).
#     Same engine as the macOS installer fetches, so a Mac and a Windows machine
#     agree on every clip length. Without it the Animator still builds, but the
#     lengths are estimates and it says so on screen.
# ---------------------------------------------------------------------------
function Has-Espeak { return [bool](Get-Command espeak-ng -ErrorAction SilentlyContinue) }

if (-not (Has-Espeak)) {
    Info "eSpeak NG not found - installing (Script Animator clip lengths)..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        try { & winget install --silent --source winget --accept-package-agreements --accept-source-agreements eSpeak-NG.eSpeak-NG } catch {}
    }
    if (-not (Has-Espeak)) {
        # The default MSI location, in case winget installed it without
        # refreshing this session's PATH.
        foreach ($guess in @("$env:ProgramFiles\eSpeak NG", "${env:ProgramFiles(x86)}\eSpeak NG")) {
            if (Test-Path (Join-Path $guess 'espeak-ng.exe')) {
                $userPath = [Environment]::GetEnvironmentVariable('Path','User')
                if ($userPath -notlike "*$guess*") {
                    [Environment]::SetEnvironmentVariable('Path', "$userPath;$guess", 'User')
                }
                $env:Path = "$env:Path;$guess"
                Info "eSpeak NG found at $guess"
                break
            }
        }
    }
    if (-not (Has-Espeak)) {
        Write-Host "  ! Could not install eSpeak NG automatically. The Script Animator"
        Write-Host "    will estimate clip lengths instead of measuring them (it says so"
        Write-Host "    on screen). Installer: https://github.com/espeak-ng/espeak-ng/releases"
    }
} else {
    Info "eSpeak NG already on PATH."
}

# ---------------------------------------------------------------------------
# 3. App venv + dependencies
# ---------------------------------------------------------------------------
if (Test-Path 'venv') { Info "Removing previous venv..."; Remove-Item -Recurse -Force 'venv' }
Info "Creating .\venv..."
& $py -m venv venv
$venvPy = Join-Path $root 'venv\Scripts\python.exe'
Info "Upgrading pip..."
& $venvPy -m pip install --upgrade pip wheel setuptools
Info "Installing dependencies from requirements.txt..."
& $venvPy -m pip install --no-compile -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "X Dependency install failed."; Read-Host "Press Enter to close"; exit 1 }

# ---------------------------------------------------------------------------
# 4. WhisperX (required; ~3 GB) - with a disk-space pre-check so we fail clearly
#    instead of dying mid-download with "No space left on device".
# ---------------------------------------------------------------------------
Section "WhisperX - German speech-to-text (~3 GB)"
$drive = (Get-Item $env:USERPROFILE).PSDrive
$freeGB = [math]::Round($drive.Free / 1GB, 1)
if ($freeGB -lt 7) {
    Write-Host "!! Not enough free disk space for WhisperX."
    Write-Host "   Need ~7 GB free on drive $($drive.Name): ; you have $freeGB GB."
    Write-Host "   Free up space and re-run this installer to enable Captions."
    Write-Host "   (The other four tools are set up and work now.)"
} else {
    Info "Setting up WhisperX (this can take 10-15 minutes)..."
    Push-Location (Join-Path $root 'tools\captions-de')
    & $py install.py
    if ($LASTEXITCODE -ne 0) { Write-Host "(WhisperX setup failed - Captions won't run until this succeeds.)" }
    Pop-Location
}

# ---------------------------------------------------------------------------
# 5. Gemini API key (Camera Prompts + Animator + Captions polishing)
# ---------------------------------------------------------------------------
Section "Gemini API key (free) - used by 3 tools"
Write-Host "Get one at https://aistudio.google.com/apikey"
$envFile = Join-Path $root 'tools\captions-de\.env'
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $root 'tools\captions-de\.env.example') $envFile }
$key = Read-Host "Paste your key (or press Enter to skip)"
if ($key) { & $venvPy (Join-Path $root 'scripts\upsert_env.py') 'GEMINI_API_KEY' $key }
else { Write-Host "Skipped. Add it later in Settings inside the app." }

# ---------------------------------------------------------------------------
# 6. Desktop + Start-Menu shortcut (pinnable, with the app icon)
# ---------------------------------------------------------------------------
Info "Creating a shortcut with the app icon (pinnable to the taskbar)..."
$pyw  = Join-Path $root 'venv\Scripts\pythonw.exe'
$icon = Join-Path $root 'brand\AppIcon.ico'
$sm   = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
# The AppUserModelID must match the one the app sets at runtime
# (src/core.py:APP_USER_MODEL_ID) so Windows shows "Mariposa Studio" (not
# "Python") on the taskbar and "Pin to taskbar" works correctly.
$appId = 'Mariposa.Studio'
$mk    = Join-Path $root 'scripts\new-shortcut.ps1'
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mariposa Studio.lnk'),
                   (Join-Path $sm 'Mariposa Studio.lnk'))) {
    try {
        & $mk -LnkPath $lnk -Target $pyw -Arguments 'src\studio.py' `
              -WorkDir $root -Icon $icon -Desc 'Mariposa Studio' -AppId $appId
    } catch { Write-Host "  ! Could not create $lnk : $($_.Exception.Message)" }
}

Section "Mariposa Studio is fully installed!"
Write-Host "Opening the app now. Next time: use the 'Mariposa Studio' shortcut"
Write-Host "(right-click -> Pin to taskbar) or 'Mariposa Studio.bat'."
Write-Host ""
Write-Host "First launch only: SmartScreen may warn -> More info -> Run anyway."

# 7. Launch the app.
Start-Process -FilePath $pyw -ArgumentList 'src\studio.py' -WorkingDirectory $root
Start-Sleep -Seconds 1
