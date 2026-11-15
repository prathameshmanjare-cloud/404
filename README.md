# CodeForge (team 404)

How to install and run the project on your machine.

## 1. Requirements

- **Python 3.10 or newer** (3.11 recommended)
- **Node.js 20 or newer** (with npm)
- **Git**
- A **Groq API key** (optional: without it, only built-in example tasks such as palindrome or prime work)

On Windows, run the backend inside WSL.

## 2. Get the code

```bash
git clone https://github.com/prathameshmanjare-cloud/404.git
cd 404
```

## 3. Set up the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `backend/.env` and paste your Groq key:

```
GROQ_API_KEY=your-key-here
LLM_MODE=live
```

## 4. Set up the frontend

Open a second terminal in the project folder:

```bash
cd frontend
npm install
cp .env.example .env
```

`frontend/.env` already points to the backend: `VITE_BACKEND_URL=http://localhost:8000`.

## 5. Run the project

Keep two terminals open.

**Terminal 1: backend** (http://localhost:8000)

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --port 8000
```

**Terminal 2: frontend** (http://localhost:5173)

```bash
cd frontend
npm run dev
```

Open **http://localhost:5173** in your browser.

## 6. Use it

1. Choose an account under **Signed in as**: **Student A**, **Student B**, **Student C** or **Instructor**.
2. Type a request in **New request**, for example
   `Write a Python function that checks if a string is a palindrome`, and press **Send**.
3. Look at the result in the tabs: **Overview**, **Rules**, **Code**, **Tests**, **Run**, **Test results**.
4. Requests that need approval (for example reading a file) appear for the **Instructor** under
   **Waiting for approval**.

## 7. Run the tests (optional)

```bash
cd backend
source .venv/bin/activate
python -m pytest -q tests
python -m evals.run_gate --hard-only
python -m evals.rules_suite
```

## Troubleshooting

| Problem | Fix |
|---|---|
| Page says "The backend is not running" | Start terminal 1 (step 5) and reload the page. |
| Yellow banner "No Groq API key loaded" | Add `GROQ_API_KEY` to `backend/.env`, then restart the backend. |
| `Address already in use` on port 8000 | Stop the other backend, or run `uvicorn main:app --port 8001` and set `VITE_BACKEND_URL=http://localhost:8001` in `frontend/.env`. |
| Changes in backend code do not show up | Stop the backend (Ctrl+C) and start it again. |
| `ModuleNotFoundError` in the backend | Activate the virtual environment: `source .venv/bin/activate`. |
