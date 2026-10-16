# ERP_AI

A mock ERP/LMS platform featuring course management, assignment viewing, and file uploads, built with FastAPI and PostgreSQL.

## Prerequisites

- Python 3.11+
- Git
- VS Code with Live Server extension
- PostgreSQL (the setup script attempts installation where supported)

## Setup & Run

### 1. Clone the repository

```bash
git clone https://github.com/shiv-pratap-dev/ERP_AI.git
cd ERP_AI
```

### 2. Create and activate a virtual environment

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Initialize the database

Run the setup script from the project root:

```bash
python setup_database.py
```

The script attempts to:

- Detect PostgreSQL and install it if missing.
- Start PostgreSQL and initialize a cluster where supported.
- Create the `erp_ai` database if it doesn't exist.
- Initialize the schema using `database/schema.sql`.
- Load initial data using `database/seed.sql`.
- Customize assignments using `database/seed2.py`.
- Generate a JWT secret if one isn't already configured.
- Create or update the local `.env` file.
- Verify database tables and display row counts.

Follow any prompts for database credentials or administrator permissions.

**Note:** Automatic installation depends on the operating system and available package manager. Some systems may require manual installation or configuration.

### 5. Start the backend

From the repository root:

```bash
python -m uvicorn backend.main:app --reload
```

Backend: http://127.0.0.1:8000

Keep the backend running while using the application.

### 6. Start the frontend

Open `frontend/index.html` using the **Live Server** extension in VS Code.

Keep the backend running while using the frontend.

## Environment & Security

- The setup script creates or updates `.env` with the configuration it manages.
- Each developer should use their own JWT secret.
- Never commit `.env` or share private credentials.
- Ensure `.env` is included in `.gitignore`.

## Dependencies

Python dependencies are listed in `requirements.txt`.

Ensure `python-multipart` is included for assignment file uploads.

## Troubleshooting

- **PostgreSQL connection errors:** Verify that PostgreSQL is running and the configured credentials are correct.
- **Missing dependencies:** Activate the virtual environment and run `pip install -r requirements.txt`.
- **Authentication errors:** Check the JWT configuration in `.env`.
- **Import errors:** Run the backend command from the repository root.
- **Setup errors:** Read the error printed by `setup_database.py` and resolve the reported issue before rerunning.

## Project Structure

```text
ERP_AI/
├── backend/
├── frontend/
├── database/
│   ├── schema.sql
│   ├── seed.sql
│   └── seed2.py
├── setup_database.py
├── requirements.txt
├── .env
├── .gitignore
└── README.md
```