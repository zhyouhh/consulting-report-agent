param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvDir = Join-Path $root ".venv"
$venvPython = Join-Path $venvDir "Scripts\\python.exe"
$tokenFile = Join-Path $root "managed_client_token.txt"
$searchPoolFile = Join-Path $root "managed_search_pool.json"
$searchPoolBackupFile = "$searchPoolFile.bundle.bak"
$modelsUrl = "https://newapi.z0y0h.work/client/v1/models"
$generatedManagedTokenFile = $false
$stagedSearchPoolFile = $false
$restoreSearchPoolFile = $false

function Invoke-Step {
    param(
        [string]$Label,
        [scriptblock]$Action
    )
    Write-Host ""
    Write-Host $Label
    & $Action
}

function Invoke-CommandChecked {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $FilePath $($Arguments -join ' ')"
    }
}

function Cleanup-BundleFiles {
    if ($generatedManagedTokenFile -and (Test-Path -LiteralPath $tokenFile)) {
        Remove-Item -LiteralPath $tokenFile -Force
    }

    if ($restoreSearchPoolFile) {
        Copy-Item -LiteralPath $searchPoolBackupFile -Destination $searchPoolFile -Force
        if (Test-Path -LiteralPath $searchPoolBackupFile) {
            Remove-Item -LiteralPath $searchPoolBackupFile -Force
        }
    } elseif ($stagedSearchPoolFile -and (Test-Path -LiteralPath $searchPoolFile)) {
        Remove-Item -LiteralPath $searchPoolFile -Force
    }
}

function Invoke-PythonFromStdin {
    param(
        [string]$Code
    )
    $Code | & $venvPython -
    if ($LASTEXITCODE -ne 0) {
        throw "Python validation script failed."
    }
}

try {
    Write-Host "========================================"
    Write-Host "Consulting Report Agent - Windows Build"
    Write-Host "========================================"

    Invoke-Step "[1/10] Check Python..." {
        $pythonCmd = Get-Command python -ErrorAction Stop
        & $pythonCmd.Source --version
        if ($LASTEXITCODE -ne 0) {
            throw "Python was not found."
        }
    }

    Invoke-Step "[2/10] Prepare project venv..." {
        if (-not (Test-Path -LiteralPath $venvPython)) {
            Invoke-CommandChecked -FilePath "python" -Arguments @("-m", "venv", ".venv")
        }
    }

    Invoke-Step "[3/10] Install backend dependencies..." {
        Invoke-CommandChecked -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip")
        Invoke-CommandChecked -FilePath $venvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
    }

    Invoke-Step "[4/10] Prepare managed client token..." {
        if (Test-Path -LiteralPath $tokenFile) {
            return
        }
        if (-not $env:CONSULTING_REPORT_MANAGED_CLIENT_TOKEN) {
            throw "Missing managed_client_token.txt or CONSULTING_REPORT_MANAGED_CLIENT_TOKEN."
        }
        Set-Content -LiteralPath $tokenFile -Value $env:CONSULTING_REPORT_MANAGED_CLIENT_TOKEN -Encoding utf8NoBOM
        $generatedManagedTokenFile = $true
    }

    Invoke-Step "[5/10] Prepare managed search pool..." {
        if (Test-Path -LiteralPath $searchPoolFile) {
            return
        }
        if (-not $env:CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE) {
            throw "Missing managed_search_pool.json or CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE."
        }
        if (-not (Test-Path -LiteralPath $env:CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE)) {
            throw "CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE points to a missing file."
        }
        Copy-Item -LiteralPath $env:CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE -Destination $searchPoolFile -Force
        $stagedSearchPoolFile = $true
    }

    Invoke-Step "[6/10] Validate managed client token..." {
        $tokenValidationCode = @(
            "from pathlib import Path"
            "from build_support import validate_bundle_managed_client_token"
            ""
            "validate_bundle_managed_client_token(Path('.'), 'managed_client_token.txt', '$modelsUrl')"
        ) -join "`n"
        Invoke-PythonFromStdin $tokenValidationCode
    }

    Invoke-Step "[7/10] Validate managed search pool..." {
        $searchPoolValidationCode = @(
            "from pathlib import Path"
            "from build_support import validate_bundle_managed_search_pool"
            ""
            "validate_bundle_managed_search_pool(Path('.'), 'managed_search_pool.json')"
        ) -join "`n"
        Invoke-PythonFromStdin $searchPoolValidationCode
    }

    Invoke-Step "[8/10] Build frontend..." {
        Push-Location (Join-Path $root "frontend")
        try {
            Invoke-CommandChecked -FilePath "npm" -Arguments @("install")
            Invoke-CommandChecked -FilePath "npm" -Arguments @("run", "build")
        } finally {
            Pop-Location
        }
    }

    Invoke-Step "[9/10] Install PyInstaller..." {
        Invoke-CommandChecked -FilePath $venvPython -Arguments @("-m", "pip", "install", "pyinstaller")
    }

    Invoke-Step "[10/10] Package application..." {
        Invoke-CommandChecked -FilePath $venvPython -Arguments @("-m", "PyInstaller", "consulting_report.spec")
    }

    Cleanup-BundleFiles

    # Flush Windows Explorer icon cache so the new exe icon shows immediately
    Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:LOCALAPPDATA\IconCache.db" -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\iconcache_*.db" -Force -ErrorAction SilentlyContinue
    Start-Process explorer

    Write-Host ""
    Write-Host "========================================"
    Write-Host "Build completed."
    Write-Host "Output directory: dist"
    Write-Host "========================================"
    exit 0
} catch {
    Write-Host ""
    Write-Error $_
    Cleanup-BundleFiles
    if (-not $NoPause) {
        cmd /c pause
    }
    exit 1
}
