#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found."
  echo "Install Python 3.11 or newer, then run this script again."
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Creating a virtual environment..."
  python3 -m venv .venv
fi

echo "Installing Python packages. The first run can take a few minutes..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f ".env" ]]; then
  echo
  echo "First-time database setup."
  echo "PostgreSQL must already be installed and running."
  echo "If asked, type the password for your PostgreSQL user."
  echo
  .venv/bin/python setup_database.py
fi

echo
echo "Server: http://127.0.0.1:8000"
echo
echo "Demo password for every account: Demo@123"
echo "  Student   student1@erp.test"
echo "  Teacher   teacher1@erp.test"
echo "  Admin     admin@erp.test"
echo
echo "Leave this window open. Press Ctrl+C to stop the server."
echo
exec .venv/bin/python -m uvicorn --app-dir backend main:app --host 127.0.0.1 --port 8000
