# ERP_AI assignment upload integration

## Files
- `backend/assignment_upload.py`: authenticated upload/details/download API routes.
- `frontend/LMS/upload.html`: upload page matching the existing ERP navbar/sidebar.

## 1) Copy files into your repo
Copy `backend/assignment_upload.py` beside your existing `main.py` **if main.py is in the backend directory**.
Copy `frontend/LMS/upload.html` into your existing `frontend/LMS/` directory.

The upload directory is resolved relative to the project root:
`<project-root>/database/assignments/`
It is created automatically and is not hard-coded to Shiv's `/Users/...` path.

## 2) Install the required packages
Activate the same virtual environment as the API:
```bash
pip install "psycopg[binary]" python-multipart
```
Keep your existing `fastapi`, `uvicorn`, `PyJWT`, and `python-dotenv` dependencies too. Add `python-multipart` to requirements.txt so friends get it when setting up.

## 3) Register the router in main.py
After `app = FastAPI(...)` and after creating `app`, add:
```python
from assignment_upload import router as assignment_upload_router
app.include_router(assignment_upload_router)
```
Python imports are conventionally placed at the top; because assignment_upload imports `verify_token` lazily inside a request, it can safely be imported at the top of main.py. Better form:
- Add `from assignment_upload import router as assignment_upload_router` beside your other imports.
- Add `app.include_router(assignment_upload_router)` immediately after `app = FastAPI(...)`.

If `main.py` is in `backend/app/` rather than `backend/`, adjust module import to match your actual layout and set `ERP_PROJECT_ROOT` if the default parent-parent resolution does not point at repository root.

## 4) Update the eye-button click in view.html
Find:
```js
row.querySelector(".eye-button").addEventListener("click", () => {
  console.log("Selected assignment:", assignment);
  alert("Assignment details will be implemented next.");
});
```
Replace it with:
```js
row.querySelector(".eye-button").addEventListener("click", () => {
  sessionStorage.setItem("selectedAssignment", JSON.stringify(assignment));
  window.location.href = "/LMS/upload.html";
});
```
This preserves the selected course in localStorage and passes the chosen assignment in sessionStorage.

## 5) Important due-date behavior
This implementation follows your stated rule:
- A first upload is allowed even if the due date has passed, because you said every assignment should be uploadable for now.
- If a student already has a submission, replacing it is allowed only up to and including the due date.
- A first submission after the due date is stored with status `late`.
If instead all first uploads should also be blocked after due date, add a server-side check for `now > due_date` before the insert.

## 6) Storage and Git
Uploaded files are written to `<project-root>/database/assignments/`. Add this to `.gitignore` so student uploads are not committed:
```gitignore
/database/assignments/*
!/database/assignments/.gitkeep
```
Add an empty `.gitkeep` file if you want Git to preserve the directory. Each collaborator's local uploads directory will be created automatically when the API starts or an upload is attempted.

## 7) Verify
1. Restart FastAPI.
2. Open a course in Transactions and go to View.
3. Click the eye icon, choose a PDF/DOCX up to 3 MB, and upload.
4. Confirm a row appears in `submissions` for the assignment/student.
5. Upload a second file before due date: the existing submission row should update, not duplicate.
6. Check `database/assignments/` for the stored file.
