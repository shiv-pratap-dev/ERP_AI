# ERP_AI

A mock ERP/LMS platform featuring course management, assignment viewing, and file uploads, built with FastAPI and PostgreSQL.

## Prerequisites

- Python 3.11+
- PostgreSQL
- VS Code with Live Server extension

## Setup & Run

### 1. Clone the repository

```bash
git clone <repository-url>
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
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root and configure the required database credentials and application settings.

### 5. Start the backend

From the project root, run:

```bash
python -m uvicorn backend.main:app --reload
```

Backend: http://127.0.0.1:8000

### 6. Start the frontend

Open `frontend/index.html` using the **Live Server** extension in VS Code.

Keep the backend running while using the application.

## Dependencies

Python dependencies are listed in `requirements.txt`.

Ensure `python-multipart` is included for assignment file uploads.