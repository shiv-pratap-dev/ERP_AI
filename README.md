# ERP AI

A local college ERP you can run on your own PC. One Python server serves the website and the API. PostgreSQL stores the demo college data.

Students can use the home dashboard, LMS assignments, academic details, examination (timetable, admit card, backlog), and Pay Online. Teachers get a separate faculty home with LMS, academic tools, examination mark entry, payroll, establishment, and OBE pages. An optional AI assistant can answer questions about the signed-in user's data.

You do **not** need Node.js, Docker, or the VS Code Live Server extension. Open the app in a normal browser after the server starts.

---

## Easiest way: download the zip and launch

This is the shortest path. The zip already contains the code, the database scripts, `requirements.txt`, and the launch scripts.

1. Install the two programs below (Python and PostgreSQL). Do this once.
2. Download **[ERP_AI-local.zip](https://github.com/shiv-pratap-dev/ERP_AI/raw/main/ERP_AI-local.zip)** from this repository.
3. Unzip it. You should see a folder named `ERP_AI` with `start.bat` inside it.
4. Launch the server:
   - **Windows:** double-click `start.bat`.
   - **macOS / Linux:** open a terminal in that folder and run `chmod +x start.sh && ./start.sh`.
5. When the window says the server is up, open **http://127.0.0.1:8000** in Chrome or Edge.
6. Sign in with a demo account from the table below.

The first launch creates a virtual environment, installs the Python packages, and sets up the `erp_ai` database. If PostgreSQL asks for a password, type the password you chose when you installed PostgreSQL (often the `postgres` user). Later launches skip setup and only start the server.

Leave the black window open while you use the site. Close that window, or press `Ctrl+C`, to stop the server.

---

## What to install first

Install these before you unzip or clone. Both are free.

### 1. Python 3.11 or newer

This project is developed on Python 3.12. Python 3.11 and 3.12 both work.

- Download: https://www.python.org/downloads/
- On the Windows installer, check **Add python.exe to PATH**.
- Confirm in a new terminal:

```powershell
python --version
```

You should see `Python 3.11` or higher. If Windows opens the Microsoft Store instead, install Python from the link above and open a new terminal.

### 2. PostgreSQL

The app stores users, courses, assignments, attendance, and marks in PostgreSQL. The setup script creates a database named `erp_ai`. It does not delete an existing database.

- Download: https://www.postgresql.org/download/
- Windows installer: https://www.postgresql.org/download/windows/
- During install, remember the password you set for the `postgres` user.
- Leave the port at **5432**.
- After install, the PostgreSQL service should be running. On Windows, open Services and confirm `postgresql` is started.

### 3. A browser, and internet for the first run

Use Chrome or Edge. The first launch downloads Python packages, and the pages load Tailwind CSS from the internet. After the packages are installed, you still want a network connection so the pages look right.

Nothing else is required. You do not need Git if you use the zip.

---

## Demo logins

Every seeded account uses the same password.

| Role | Email | Password | Where you land |
| --- | --- | --- | --- |
| Student | `student1@erp.test` | `Demo@123` | Student home |
| Student | `student2@erp.test` … `student10@erp.test` | `Demo@123` | Student home |
| Teacher | `teacher1@erp.test` | `Demo@123` | Faculty home |
| Teacher | `teacher2@erp.test` … `teacher5@erp.test` | `Demo@123` | Faculty home |
| Admin | `admin@erp.test` | `Demo@123` | Student home |

Teacher `teacher1@erp.test` is **Dr. Neha Sharma** (`EMP001`), mapped to Data Structures and Database Management Systems. The other teachers are in `database/seed.sql`.

The login session lasts 30 minutes. Sign in again after that.

---

## Other way: clone the repository

Use this if you want the Git history instead of the zip. The steps after cloning are the same.

```bash
git clone https://github.com/shiv-pratap-dev/ERP_AI.git
cd ERP_AI
```

Then launch:

- Windows: double-click `start.bat`
- macOS / Linux:

```bash
chmod +x start.sh
./start.sh
```

Open http://127.0.0.1:8000

---

## What the launch script does

`start.bat` and `start.sh` run these steps from the project folder:

1. Create `.venv` if it does not exist.
2. Install every package listed in `requirements.txt`.
3. If `.env` is missing, run `python setup_database.py`.
4. Start the server on http://127.0.0.1:8000

`setup_database.py` then:

- Finds PostgreSQL, and on a supported machine offers to install it if the tools are missing.
- Creates the `erp_ai` database when it does not exist.
- Loads `database/schema.sql` and `database/seed.sql`.
- Customizes assignment text with `database/seed2.py`.
- Writes a local `.env` with a generated `JWT_SECRET_KEY` and your database settings.
- Prints table row counts when it succeeds.

The script never drops a database and never deletes existing rows.

---

## Run the steps yourself

Use this when you want to see each command. Run them from the project folder (the folder that contains `setup_database.py`).

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python setup_database.py
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

If PowerShell blocks the activate script, run this once, then try the activate line again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python setup_database.py
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Keep that terminal open. The site and the API are both on port **8000**. Do not switch the port. Several pages call `http://127.0.0.1:8000` directly.

Useful addresses after you sign in:

| Page | Address |
| --- | --- |
| Sign in | http://127.0.0.1:8000 |
| Student home | http://127.0.0.1:8000/home |
| Faculty home | http://127.0.0.1:8000/faculty/home |
| API docs | http://127.0.0.1:8000/docs |

---

## Optional: AI assistant

The chat assistant calls Google Gemini. The rest of the ERP works with no API key. Chat replies need a key.

1. Create a key at https://aistudio.google.com/apikey
2. Open the `.env` file in the project folder (the launch script creates it).
3. Add these lines. Use your real key. Do not share that file.

```env
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.5-flash
```

4. Stop the server and start it again.

`.env.example` lists every setting the app reads. `setup_database.py` fills in the database lines and the JWT secret for you.

| Variable | Purpose |
| --- | --- |
| `JWT_SECRET_KEY` | Signs the login cookie. Generated for you. |
| `PGHOST` | Database host. Usually `localhost`. |
| `PGPORT` | Database port. Usually `5432`. |
| `PGUSER` | Database user. Usually `postgres` on Windows. |
| `PGPASSWORD` | Password for that user. |
| `PGDATABASE` | Database name. The setup script uses `erp_ai`. |
| `GEMINI_API_KEY` | Optional. Only the AI assistant needs it. |
| `GEMINI_MODEL` | Optional model id. Default is `gemini-3.5-flash`. |

Never commit `.env`. It is listed in `.gitignore`.

---

## What you can click through

**Student** (`student1@erp.test`)

- Home dashboard
- LMS: view assignments, upload a submission, see the transaction list
- Academic student detail
- Examination: timetable, admit card, backlog
- Pay Online

**Teacher** (`teacher1@erp.test`)

- Faculty home
- LMS: pick a course, create an assignment, check submissions
- Academic: teaching plan, attendance, attendance register, extra class
- Examination mark entry
- Payroll payslip
- Establishment: leave card, OD application, comp off, service book
- OBE: portal, question paper, question-wise marks, mark report

Demo names, fees, and marks are synthetic. They are safe to reset by creating a fresh `erp_ai` database on your machine.

---

## Python packages

Install them with:

```bash
pip install -r requirements.txt
```

The same list is in `backend/requirements.txt`.

| Package | Why it is here |
| --- | --- |
| `fastapi`, `uvicorn[standard]` | Web server and API |
| `psycopg[binary]` | PostgreSQL driver |
| `python-dotenv` | Reads `.env` |
| `bcrypt`, `pwdlib[argon2]`, `PyJWT` | Password checks and login tokens |
| `pydantic[email]` | Request validation |
| `python-multipart` | Assignment file uploads |
| `langgraph`, `langchain-google-genai`, `langchain-core` | AI assistant |

---

## Project layout

```text
ERP_AI/
├── start.bat                 Windows: double-click to launch
├── start.sh                  macOS/Linux: ./start.sh
├── setup_database.py         Creates erp_ai and writes .env
├── requirements.txt          Python packages
├── .env.example              Setting names (no secrets)
├── ERP_AI-local.zip          Ready-to-unzip copy of this project
├── backend/                  FastAPI app and module APIs
├── frontend/                 HTML, CSS, and JavaScript
│   ├── index.html            Sign-in page
│   ├── home.html             Student home
│   ├── LMS/  Academic/  Examination/  PayOnline/
│   └── faculty/              Teacher pages
└── database/
    ├── schema.sql
    ├── seed.sql              Demo users and courses
    └── seed2.py              Course-specific assignment text
```

---

## If something fails

**`python` is not recognized**  
Install Python 3.11+ and check **Add python.exe to PATH**. Close the old terminal and open a new one.

**PostgreSQL connection error, or the script asks for a password and then fails**  
Confirm the PostgreSQL service is running. The usual Windows user is `postgres`, port `5432`. You can write the password into `.env` and run `python setup_database.py` again:

```env
PGHOST=localhost
PGPORT=5432
PGUSER=postgres
PGPASSWORD=the-password-you-chose
PGDATABASE=erp_ai
```

**`Set a real JWT_SECRET_KEY in your .env file`**  
Run `python setup_database.py` from the project folder. It writes a real secret. Do not leave the placeholder value in `.env`.

**Package install failed**  
Activate `.venv`, check your internet connection, and run `pip install -r requirements.txt` again.

**Port 8000 is already in use**  
Stop the other program that is using port 8000, then start this server again. This app is written to be opened at http://127.0.0.1:8000.

**The page is unstyled or looks like plain text**  
The browser could not load the Tailwind CDN. Connect to the internet and refresh.

**Sign-in says the email or password is wrong**  
Use the addresses in the demo table, including `@erp.test`, and the password `Demo@123`. Database setup must have finished successfully first.

**AI chat says the Gemini key is missing**  
Add `GEMINI_API_KEY` to `.env` and restart the server. The other pages still work without it.

**`Import` errors or `backend.main` not found**  
Start the server from the project folder, not from inside `backend/`:

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

**Setup says it failed**  
Read the line that starts with `SETUP FAILED` or `ERROR`. Fix that issue and run `start.bat` or `python setup_database.py` again. Your existing database is not dropped.
