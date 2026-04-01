$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvDir = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

function Get-PythonCommand {
    foreach ($candidate in @("python", "py")) {
        try {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
        }
    }

    throw "Python nao foi encontrado no PATH. Instala o Python 3 e tenta novamente."
}

if (-not (Test-Path $venvPython)) {
    $pythonCmd = Get-PythonCommand

    if ($pythonCmd -eq "py") {
        & py -3 -m venv $venvDir
    }
    else {
        & python -m venv $venvDir
    }
}

& $venvPython -m pip install -r requirements.txt
& $venvPython -m streamlit run app.py
