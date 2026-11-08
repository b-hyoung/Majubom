# The Pi serves C920 video; the analysis PC reads its own USB2.0 camera.
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repository '.venv\Scripts\python.exe'
$launcher = Join-Path $PSScriptRoot 'tools\start_mac_monitor.py'
$profile = Join-Path $PSScriptRoot 'config\installation-20261107\pi-usb-test.json'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python environment not found. Follow docs/guides/PI_USB_WINDOWS.md first.'
}
& $python $launcher --test-profile $profile @args
exit $LASTEXITCODE
