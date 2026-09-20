$ErrorActionPreference = "Stop"

$AppDirectory = $PSScriptRoot
$BuildEnvironment = Join-Path $AppDirectory ".build-venv"
$BuildPython = Join-Path $BuildEnvironment "Scripts\python.exe"
$BuildDirectory = Join-Path $AppDirectory ".build"
$DesktopDirectory = [Environment]::GetFolderPath("DesktopDirectory")
$ExecutablePath = Join-Path $DesktopDirectory "Tari tracker.exe"

if (-not (Test-Path -LiteralPath $DesktopDirectory -PathType Container)) {
    throw "Le Bureau Windows est introuvable : $DesktopDirectory"
}

if (-not (Test-Path -LiteralPath $BuildPython -PathType Leaf)) {
    & python -m venv $BuildEnvironment
    if ($LASTEXITCODE -ne 0) { throw "Impossible de préparer l'environnement de compilation." }
}

& $BuildPython -m pip install --disable-pip-version-check -r (Join-Path $AppDirectory "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Installation de PyInstaller échouée." }

& $BuildPython -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --windowed `
    --name "Tari tracker" `
    --distpath $DesktopDirectory `
    --workpath (Join-Path $BuildDirectory "work") `
    --specpath $BuildDirectory `
    (Join-Path $AppDirectory "tari_tracker.py")
if ($LASTEXITCODE -ne 0) { throw "La compilation de Tari tracker a échoué." }

if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "L'exécutable attendu n'a pas été créé : $ExecutablePath"
}

Write-Host "Tari tracker est prêt : $ExecutablePath"
