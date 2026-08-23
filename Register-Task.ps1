<#
    Registers a Windows Scheduled Task that runs collect.py every 5 minutes.

    This keeps logging whenever the laptop is ON, even with the GUI closed.
    It cannot log while the laptop is off or asleep -- that is what the
    GitHub Actions collector in .github/workflows/collect-parking.yml covers.

    Run once:   powershell -ExecutionPolicy Bypass -File Register-Task.ps1
    Custom gap: powershell -ExecutionPolicy Bypass -File Register-Task.ps1 -IntervalMinutes 10
    Check it:   Get-ScheduledTask "NCSU Parking Logger" | Get-ScheduledTaskInfo
    Remove it:  Unregister-ScheduledTask -TaskName "NCSU Parking Logger" -Confirm:$false
#>
param(
    [int]$IntervalMinutes = 5,
    [string]$TaskName = 'NCSU Parking Logger'
)

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Root 'collect.py'

if (-not (Test-Path $Script)) { throw "collect.py not found next to this script." }

# Resolve the real interpreter rather than the WindowsApps alias shim -- the
# alias is a reparse point that Task Scheduler cannot always follow. pythonw
# is used so no console window flashes every 5 minutes.
$pyDir  = & python -c "import sys, os; print(os.path.dirname(sys.executable))"
$pythonw = Join-Path $pyDir 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = Join-Path $pyDir 'python.exe' }
if (-not (Test-Path $pythonw)) { throw "Could not locate a Python interpreter." }

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$Script`"" -WorkingDirectory $Root

# Repeat indefinitely. -Once + RepetitionInterval is the only way to get a
# sub-hourly repeat out of the ScheduledTasks module.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description 'Logs NC State live parking availability into parking.db.' | Out-Null

"Registered '$TaskName'"
"  interpreter : $pythonw"
"  every       : $IntervalMinutes minutes"
"  database    : $(Join-Path $Root 'data\parking.db')"
""
"Runs only while the laptop is on. Set up the GitHub Actions collector for"
"coverage while it is off, then press 'Sync cloud' in the GUI."
