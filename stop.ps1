# Stops the API and the web app started for PRAMANA (the datastores keep running: `docker compose stop` stops those).
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*uvicorn pramana.api.app*') -or
    ($_.Name -eq 'node.exe' -and $_.CommandLine -like '*pramana-v2*frontend*next*')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
