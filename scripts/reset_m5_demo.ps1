param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("m5_start", "m5_showcase")]
    [string]$Scenario,

    [switch]$ConfirmResetM5Demo
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DatabaseName = "math_agent_m5_demo"
$DatabasePort = "55432"
$Python = Join-Path $ProjectRoot ".conda-env\python.exe"
$DropDb = Join-Path $ProjectRoot ".conda-env\Library\bin\dropdb.exe"
$CreateDb = Join-Path $ProjectRoot ".conda-env\Library\bin\createdb.exe"
$Backend = Join-Path $ProjectRoot "backend"

if (-not $ConfirmResetM5Demo) {
    throw "Reset deletes the isolated math_agent_m5_demo database. Pass -ConfirmResetM5Demo explicitly."
}
if ($DatabaseName -ne "math_agent_m5_demo") {
    throw "Safety check failed: target is not the allowed M5 demo database."
}
foreach ($Path in @($Python, $DropDb, $CreateDb, $Backend)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Required project path is missing: $Path"
    }
}

Write-Host "Rebuilding isolated demo database: ${DatabaseName} (localhost:${DatabasePort})"
Write-Host "The default math_agent development database will not be modified."

& $DropDb --if-exists --host localhost --port $DatabasePort --username postgres $DatabaseName
if ($LASTEXITCODE -ne 0) { throw "Failed to drop the isolated demo database." }
& $CreateDb --host localhost --port $DatabasePort --username postgres $DatabaseName
if ($LASTEXITCODE -ne 0) { throw "Failed to create the isolated demo database." }

$PreviousDatabaseUrl = $env:DATABASE_URL
$PreviousAppEnv = $env:APP_ENV
try {
    $env:APP_ENV = "demo"
    $env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:$DatabasePort/$DatabaseName"
    Push-Location $Backend
    try {
        & $Python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
        & $Python -m scripts.seed_database
        if ($LASTEXITCODE -ne 0) { throw "Seed import failed." }
        & $Python -m scripts.rebuild_mastery
        if ($LASTEXITCODE -ne 0) { throw "Mastery rebuild failed." }
        & $Python -m scripts.prepare_m5_demo --scenario $Scenario
        if ($LASTEXITCODE -ne 0) { throw "M5 scenario preparation failed." }
        & $Python -m scripts.verify_m5
        if ($LASTEXITCODE -ne 0) { throw "M5 scenario verification failed." }
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:DATABASE_URL = $PreviousDatabaseUrl
    $env:APP_ENV = $PreviousAppEnv
}

Write-Host "M5 isolated demo database is ready: $Scenario"
