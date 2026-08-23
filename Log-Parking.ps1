<#
    Logs NC State live parking-lot occupancy to a CSV.

    Data source: the same public endpoint that powers the "Parking Availability"
    table on https://transportation.ncsu.edu/ (and the OnCampus app's live counts).
    One row is appended per lot per poll.

    Usage:  powershell -ExecutionPolicy Bypass -File Log-Parking.ps1
#>

param(
    # Bypass their CDN cache and read the origin directly. Off by default: the
    # cached copy is only seconds behind, and for Spring Hill specifically it
    # tested identical. Only worth it if you need the busy decks exact.
    [switch]$Fresh
)

$ErrorActionPreference = 'Stop'

$Root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root 'data'
$Csv     = Join-Path $DataDir 'parking-log.csv'
$ErrLog  = Join-Path $DataDir 'errors.log'

if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }

$base = 'https://transportation.ncsu.edu/wp-json/ncsu-transportation-parking-view/v1/get-parking-data'
$url  = if ($Fresh) { "$base`?_=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())" } else { $base }

try {
    $response = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 30 -Headers @{
        'X-REQUESTED-WITH' = 'XMLHttpRequest'
        # Honest, low-volume identification. Add a contact if you like, e.g.
        # 'ncsu-parking-logger/1.0 (personal research; you@example.com)'
        # Deliberately NOT sending the x-api-key from the page source: the
        # endpoint does not require it, and borrowing it is the one thing here
        # that could be read as circumventing an access control.
        'User-Agent'       = 'ncsu-parking-logger/1.0 (personal research)'
    }
} catch {
    Add-Content -Path $ErrLog -Encoding utf8 -Value "$(Get-Date -Format o)`tfetch failed`t$($_.Exception.Message)"
    exit 1
}

# The endpoint returns an array of arrays; flatten it.
$lots = @($response | ForEach-Object { $_ })
if ($lots.Count -eq 0) {
    Add-Content -Path $ErrLog -Encoding utf8 -Value "$(Get-Date -Format o)`tempty response"
    exit 1
}

$now      = Get-Date
$utcIso   = $now.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$localIso = $now.ToString('yyyy-MM-ddTHH:mm:ss')

$rows = foreach ($lot in $lots) {
    [pscustomobject]@{
        timestamp_utc   = $utcIso
        timestamp_local = $localIso
        day_of_week     = $now.DayOfWeek
        hour_local      = $now.Hour
        minute_local    = $now.Minute
        location_name   = $lot.location_name
        free_spaces     = [int]$lot.free_spaces
        total_spaces    = [int]$lot.total_spaces
        occupancy_pct   = [int]$lot.occupancy
    }
}

if (Test-Path $Csv) {
    $rows | Export-Csv -Path $Csv -NoTypeInformation -Encoding utf8 -Append
} else {
    $rows | Export-Csv -Path $Csv -NoTypeInformation -Encoding utf8
}

$sh = $rows | Where-Object { $_.location_name -like '*Spring Hill*' }
if ($sh) { "$localIso  Spring Hill: $($sh.free_spaces) free of $($sh.total_spaces) ($($sh.occupancy_pct)% full)" }
else     { "$localIso  logged $($rows.Count) lots (no Spring Hill row in this response)" }
