param(
  [string]$Port = "5000"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path ".venv")) {
  py -m venv .venv
}

.\.venv\Scripts\activate
pip install -r requirements.txt

$env:FLASK_PORT = $Port
py desktop_pet.py
