# Wrapper executed by the OsintDeploy scheduled task (runs outside interactive
# shells so long transfers cannot be killed by parent-process cleanup).
$ErrorActionPreference = 'Continue'
Set-Location 'C:\Users\Developer\realtime-osint'
'DEPLOY_TASK_START ' + (Get-Date -Format o) | Out-File deploy_status.txt -Encoding ascii
# Run deploy.ps1 in a child process: captures Write-Host output to files and
# survives deploy.ps1's internal `exit 1` calls.
$p = Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','deploy.ps1','-Service','all') `
    -WorkingDirectory (Get-Location) -RedirectStandardOutput 'deploy_out.log' `
    -RedirectStandardError 'deploy_err.log' -NoNewWindow -Wait -PassThru
'DEPLOY_TASK_DONE ' + $p.ExitCode | Out-File deploy_status.txt -Append -Encoding ascii
