param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Distro = 'Ubuntu'
)
$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$taskCache = Join-Path $taskRoot '.dify-dev'
New-Item -ItemType Directory -Path $taskCache -Force | Out-Null
$taskRecord = Join-Path $taskCache 'wsl-keepalive.json'
if (Test-Path -LiteralPath $taskRecord) {
    $taskOld = Get-Content -LiteralPath $taskRecord -Raw | ConvertFrom-Json
    $taskExisting = Get-Process -Id $taskOld.pid -ErrorAction SilentlyContinue
    if ($taskExisting -and $taskExisting.ProcessName -eq 'wsl' -and
        $taskExisting.StartTime.ToUniversalTime().Ticks.ToString() -eq $taskOld.startedUtcTicks -and
        $taskOld.distro -eq $Distro) {
        Write-Output "WSL keeper already active (PID $($taskOld.pid))."
        return
    }
}
$taskLinuxScript = (& wsl.exe -d $Distro --exec wslpath -u (Join-Path $PSScriptRoot 'keepalive.py') | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $taskLinuxScript.StartsWith('/')) { throw 'Cannot resolve the WSL script path.' }
$taskArgs = @('-d', $Distro, '--exec', 'python3', ('"' + $taskLinuxScript + '"'))
$taskProcess = Start-Process -FilePath 'wsl.exe' -ArgumentList $taskArgs -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $taskCache 'wsl-keepalive.out') `
    -RedirectStandardError (Join-Path $taskCache 'wsl-keepalive.err')
@{pid=$taskProcess.Id; startedUtcTicks=$taskProcess.StartTime.ToUniversalTime().Ticks.ToString(); distro=$Distro} |
    ConvertTo-Json | Set-Content -LiteralPath $taskRecord -Encoding UTF8
Write-Output "WSL keeper started (PID $($taskProcess.Id)). It exits after the demo containers stop."
