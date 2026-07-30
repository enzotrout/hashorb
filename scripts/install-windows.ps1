[CmdletBinding()]
param(
    [ValidateSet("install", "upgrade", "uninstall")]
    [string]$Action = "install",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $OutputEncoding

function Invoke-Visible {
    param([Parameter(Mandatory)][string[]]$Command)

    Write-Host ("+ " + ($Command -join " "))
    if (-not $DryRun) {
        & $Command[0] $Command[1..($Command.Length - 1)]
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE."
        }
    }
}

if (-not $DryRun -and $null -eq (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required and must already be on PATH."
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($Action -eq "uninstall") {
    Invoke-Visible -Command @("uv", "tool", "uninstall", "hashphere")
    exit 0
}

Invoke-Visible -Command @("uv", "python", "find", "--no-python-downloads", "3.13")
$InstallCommand = @(
    "uv", "tool", "install", "--no-python-downloads", "--python", "3.13", "--force"
)
if ($Action -eq "upgrade") {
    $InstallCommand += "--upgrade"
}
$InstallCommand += $ProjectRoot
Invoke-Visible -Command $InstallCommand

Write-Host "Installation complete. Run 'hashsphere doctor' from your configuration directory."
