# ERP_AI

A mock ERP/LMS platform featuring course management, assignment viewing, and file uploads, built with FastAPI and PostgreSQL.

## Prerequisites

- Python 3.11+
- PostgreSQL (the setup script attempts installation where supported)
- VS Code with Live Server extension
- Git

## Setup & Run

### 1. Clone the repository

```bash
git clone https://github.com/shiv-pratap-dev/ERP_AI.git
cd ERP_AI
```

### 2. Create and activate a virtual environment

**macOS/Linux**
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

- Install PostgreSQL if it is missing and supported by the system.
- Create the `erp_ai` database if it doesn't exist.
- Initialize the database schema using `database/schema.sql`.
- Load initial data using `database/seed.sql`.
- Customize assignments using `database/seed2.py`.
- Generate a JWT secret if one is not already configured.
- Create or update the local `.env` file.
- Verify database tables and report row counts.

Follow any prompts for PostgreSQL credentials or installation permissions.

**Note:** Existing databases are not automatically reseeded. Some operating systems may require manual PostgreSQL installation or configuration.

### 5. Configure environment variables

The setup script creates or updates `.env` with the configuration it manages.

If additional database or application settings are required, configure them in `.env`.

**Security:**
- Each developer should use their own JWT secret.
- Never commit `.env` or share private credentials.
- Ensure `.env` is included in `.gitignore`.

### 6. Start the backend

From the project root:

```bash
python -m uvicorn backend.main:app --reload
```

Backend: http://127.0.0.1:8000

Keep the terminal running while using the application.

### 7. Start the frontend

Open `frontend/index.html` using the **Live Server** extension in VS Code.

Keep the backend running while using the frontend.

## Dependencies

Python dependencies are listed in `requirements.txt`.

Ensure `python-multipart` is included for assignment file uploads.

## Troubleshooting

- **Database connection errors:** Ensure PostgreSQL is running and the configured credentials are correct.
- **Missing dependencies:** Activate the virtual environment and run `pip install -r requirements.txt`.
- **Authentication errors:** Check that the JWT secret is configured correctly in `.env`.
- **Import errors:** Run the backend command from the repository root.

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