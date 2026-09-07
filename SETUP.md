# PWMS Setup Guide — Step by Step

This guide takes you from a brand-new computer with nothing installed to a
running PWMS instance (backend + frontend) that you can log into, on the
`Updates-2.0` branch.

You do not need prior Django or Angular experience — just follow the steps
in order. Commands are shown for **Windows (PowerShell)** since that's how
this project is normally run, with a macOS/Linux equivalent given wherever
it differs meaningfully.

---

## 1. What you're installing

```text
Backend  = Django (Python) — stores data, exposes the API, runs on :8000
Frontend = Angular (Node.js) — the website you use, runs on :4200
```

Both must be running at the same time for the app to work: the frontend
in your browser talks to the backend over HTTP.

---

## 2. Prerequisites

Install these first:

1. **Git** — https://git-scm.com/downloads
2. **Python 3.12** (or a recent 3.11+) — https://www.python.org/downloads/
   - On Windows, tick **"Add python.exe to PATH"** during install.
3. **Node.js** — a current LTS release (Node 20 or newer; this project was
   built and tested against Node 22). https://nodejs.org/
4. A modern browser (Chrome or Edge recommended, for browser-notification
   support later).

### Verify everything is installed

```powershell
git --version
python --version
node --version
npm --version
```

Each command should print a version number. If any says "not recognized",
that program isn't installed correctly yet — install it and reopen your
terminal before continuing.

---

## 3. Clone the repository

Pick a folder for the project, e.g.:

```powershell
cd D:\
git clone https://github.com/avviiiral/Personal_Wealth_Monitoring.git
cd Personal_Wealth_Monitoring
```

Make sure you're on the `Updates-2.0` branch (this guide assumes it):

```powershell
git checkout Updates-2.0
git pull
git branch --show-current
```

---

## 4. Backend setup

All commands in this section run from the `backend` folder.

```powershell
cd backend
```

### 4.1 Create and activate a virtual environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

macOS/Linux equivalent:

```bash
python3 -m venv venv
source venv/bin/activate
```

Your terminal prompt should now start with `(venv)`. Every command below in
this section assumes the virtual environment is active — if you close and
reopen your terminal, re-run the `Activate.ps1` line first.

> **PowerShell blocks the activation script?** Run this once, then retry:
>
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

### 4.2 Install Python dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Use the full `requirements.txt` as-is (don't add `--no-deps`) — some
packages (like `feedparser`, used by the Portfolio News agent) need their
own transitive dependencies installed to work correctly.

### 4.3 Create your environment file

Copy the template and fill it in:

```powershell
copy .env.example .env
```

Open `backend\.env` in a text editor. For local development, the only line
worth filling in right away is:

```env
GEMINI_API_KEY=YOUR_KEY_HERE
```

- Get a Gemini API key from **Google AI Studio**
  (https://aistudio.google.com/) if you don't have one.
- The Gemini key is only needed for the **AI Chat** and **Portfolio News**
  features. Everything else — portfolio tracking, holdings, analytics,
  reports, user management — works without it.
- Every other variable in `.env.example` (`SECRET_KEY`, `DEBUG`,
  `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, etc.) already has a safe default
  for local development baked into `config/settings.py` — you only need to
  set these for a real deployment, not for running this on your own
  machine. See the comments in `.env.example` for what each one does.
- **Do not commit this file.** It's already in `.gitignore`.

### 4.4 Set up the database

The project uses SQLite by default — no separate database server to
install. Just run the migrations to create the schema:

```powershell
python manage.py migrate
```

You should see a list of `Applying ... OK` lines. This step is safe to
re-run any time; it never deletes existing data.

### 4.5 Verify the backend is healthy

```powershell
python manage.py check
```

Expect: `System check identified no issues (0 silenced).`

### 4.6 Create your first user (Super User)

Because PWMS enforces roles on every request, you need at least one
account before you can log in and do anything. The very first account
should be a **Super User** (the highest-privilege role) so you can then
create everyone else from inside the app:

```powershell
python manage.py createsuperuser
```

Follow the prompts (username, email, password). This account is
automatically given the `SUPERUSER` role — you don't need any extra step.

> Forgot to create one, or need a second Super User later? Either run
> `createsuperuser` again, or once you're logged in as an existing Super
> User, use **Settings → User Management → Add User** and set the role to
> `SUPERUSER`.

### 4.7 Start the backend server

For local development:

```powershell
python manage.py runserver
```

Leave this terminal window open — it needs to keep running. You should see:

```text
Starting development server at http://127.0.0.1:8000/
```

Confirm it's actually responding by opening this URL in a browser:

```text
http://127.0.0.1:8000/api/health/
```

You should see a small JSON response with `"status": "success"`.

> **Running this closer to how it'd actually be deployed?** See
> [section 11](#11-alternative-running-via-a-real-wsgiasgi-server) for the
> `waitress`/`uvicorn` equivalents — same app, no dev-server autoreloader.

---

## 5. Frontend setup

Open a **second, separate terminal window** (leave the backend running in
the first one). From the repository root:

```powershell
cd frontend
npm install
```

This downloads all Angular dependencies — it can take a few minutes the
first time.

Then start the Angular dev server:

```powershell
npm start
```

You should see Angular compile successfully and print something like:

```text
Local:   http://localhost:4200/
```

Open that URL in your browser.

> This dev server always talks to `http://localhost:8000` (set in
> `frontend/src/environments/environment.ts`). You don't need to touch
> this for local development — it's only relevant if you're building for
> a real deployment, see [section 12](#12-building-the-frontend-for-a-real-deployment).

---

## 6. First login

1. Go to `http://localhost:4200/login`.
2. Log in with the Super User account you created in step 4.6.
3. You should land on the Dashboard. It will be empty until you add
   portfolio data (see step 8).
4. Open **Settings → Account** and confirm your Role shows `SUPERUSER`.

### Creating more users

From **Settings → User Management** (visible because you're a Super User):

1. Click **+ Add User**.
2. Fill in name/username/email/password, pick a role (Viewer or Admin —
   only a Super User can grant the Super User role), optionally assign a
   **Family Group** so this person can see your portfolio data (or theirs,
   once they have some).
3. Click **Create User**.

To let two accounts see each other's combined Dashboard/Portfolio/
Analytics/Mutual Funds data (e.g. two family members), put them in the
same **Family Group**: either set it when creating/editing a user, or use
**Manage Family Groups** to create a group and add existing users to it.
This never changes who can _edit_ anything — only what's visible.

---

## 7. Automatic background jobs — nothing to configure

You don't need to do anything for this. Four background jobs start
automatically inside the Django process the moment you run `runserver`
(or the WSGI/ASGI commands in section 11) — market price refresh (every 15
minutes), a once-a-day refresh job, an immediate post-import price fetch,
and Portfolio News monitoring (every 30 minutes, needs the Gemini key from
step 4.3). None of them need Windows Task Scheduler, a `.bat` file, or any
separate process — they run for as long as the server is up, and stop the
moment you stop it.

Check `backend\logs\pwms.log` (created automatically) to see them running.

If you want an immediate one-off price refresh instead of waiting:

```powershell
python manage.py update_market_prices
```

Or an immediate one-off news check:

```powershell
python manage.py monitor_portfolio_news
```

Watch the printed statistics (`Holdings processed`, `Articles retrieved`,
`Alerts created`, etc.) — `Alerts created: 0` is often normal on a fresh
portfolio with no recent matching news yet.

---

## 8. Getting your portfolio data in

Import transactions from an Excel workbook (a "Summary" sheet is optional
— a Transactions-only workbook is valid):

1. Log in, go to **Portfolio**.
2. Use the **Import** option and select your `.xlsx` file.
3. Every asset the import touches gets an immediate background price
   refresh (see section 7) — you don't need to wait for the scheduled run.

If holdings look off after an import, rebuild them from the transaction
history:

```powershell
python manage.py rebuild_holdings --user-id <id>
```

(Use the ID of the user you imported for — you can find it in
**Settings → User Management**.)

---

## 9. Running the test suites (optional, recommended if you plan to modify code)

Backend (from `backend/`, virtual environment active):

```powershell
python manage.py test
```

To run just the RBAC/Family Groups tests:

```powershell
python manage.py test users portfolio -v 2
```

Frontend (from `frontend/`):

```powershell
npm test
npm run build
```

---

## 10. Everyday startup (after the first-time setup above)

Once steps 1–8 are done once, starting the app again is just:

**Terminal 1:**

```powershell
cd Personal_Wealth_Monitoring\backend
.\venv\Scripts\Activate.ps1
python manage.py runserver
```

**Terminal 2:**

```powershell
cd Personal_Wealth_Monitoring\frontend
npm start
```

Then open `http://localhost:4200`.

---

## 11. Alternative: running via a real WSGI/ASGI server

`runserver` is fine for everyday development. If you want to run this
closer to how it'd actually be deployed — no autoreloader, a real
production-style server — two options, both already in `requirements.txt`:

**WSGI (waitress):**

```powershell
python -m waitress --host=127.0.0.1 --port=8000 config.wsgi:application
```

**ASGI (uvicorn):**

```powershell
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000
```

Both serve the exact same app on the same port `runserver` uses, and both
correctly start all four background jobs from section 7 automatically —
check `logs\pwms.log` to confirm.

---

## 12. Building the frontend for a real deployment

For local development, skip this — `npm start` is all you need.

Before building for anywhere other than your own machine:

1. Open `frontend/src/environments/environment.prod.ts`.
2. Change `apiUrl` from the placeholder to your real deployed backend
   address (or `''` if the frontend and backend share the same origin).
3. Build:

   ```powershell
   cd frontend
   ng build --configuration production
   ```

   This automatically swaps in `environment.prod.ts` in place of
   `environment.ts` (configured in `angular.json`) — you don't edit
   `environment.ts` itself for this.
4. On the backend side, also update `.env`'s `ALLOWED_HOSTS`,
   `CORS_ALLOWED_ORIGINS`, and `CSRF_TRUSTED_ORIGINS` to match your real
   deployed frontend origin — see `backend/.env.example`.

---

## Troubleshooting

### `python` or `pip` is not recognized

```powershell
python --version
pip --version
```

If Python is installed but the wrong interpreter runs, make sure the
virtual environment is active:

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python --version
python -m pip --version
```

Prefer `python -m pip` over a bare `pip` when diagnosing interpreter
mismatches.

### PowerShell refuses to activate the virtual environment

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\venv\Scripts\Activate.ps1
```

### Django says a module/app is missing

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py check
```

### Django says migrations are pending

```powershell
cd backend
python manage.py migrate
```

### "System check identified no issues" but the browser can't connect

Make sure the backend is actually running (`python manage.py runserver`)
and test `http://127.0.0.1:8000/api/health/` directly in a browser before
troubleshooting the frontend.

### Frontend can't reach the backend

Check `frontend/src/environments/environment.ts`'s `apiUrl` — for local
development this should be `http://localhost:8000`. If you're opening the
frontend from a different device on your network, `localhost` on that
device means itself, not your Django machine — you'd need to change
`apiUrl` to your backend machine's real address, and update
`CORS_ALLOWED_ORIGINS`/`CSRF_TRUSTED_ORIGINS` in `backend/.env` to match.
This isn't needed for normal single-machine development.

### I can't log in / there's no user yet

Run `python manage.py createsuperuser` (see step 4.6). If you already have
a user but forgot the password, an existing Super User can reset it from
**Settings → User Management → Reset Password**, or you can reset it
directly:

```powershell
python manage.py changepassword <username>
```

### A Viewer/Admin account can't see something I expect

Check three things, in order:

1. **Role** — Viewer accounts can't edit prices or manage users by design;
   that's expected, not a bug.
2. **Ownership** — portfolio data belongs to whoever entered it. A brand
   new account has no data of its own until you add some or share access.
3. **Family Group** — if you expect two accounts to see combined data,
   confirm both are in the _same_ Family Group under
   **Settings → User Management → Manage Family Groups**.

### An Admin can't do something involving a Super User account

This is by design — an Admin can never edit, deactivate, delete, reset the
password of, or change the Family Group of a Super User account. Only
another Super User can do that.

### "Cannot deactivate/delete the last active Super User"

The system deliberately refuses to leave itself with zero Super Users.
Create or promote a second Super User first if you need to remove one.

### The Portfolio/Dashboard numbers look wrong after sharing a Family Group

Manual price edits and any write action are still scoped to the actual
owner — sharing visibility never changes who can edit what. If a combined
total looks off, check each member's own data individually first
(temporarily removing them from the group, or checking
`Settings → Manual Prices`, is the fastest way to isolate it).

### The news page is empty

```powershell
cd backend
python manage.py check
python manage.py migrate
python manage.py monitor_portfolio_news
```

Read the printed counts. Common reasons for zero alerts: the user has no
active (non-zero-quantity) holdings, no recent matching news exists, the
Gemini key is missing/invalid, or every candidate article was already
processed in a previous run.

### The monitor says Gemini is skipped

Make sure `backend/.env` has `GEMINI_API_KEY=...` (or `GOOGLE_API_KEY=...`),
then restart `runserver` and re-run
`python manage.py monitor_portfolio_news`.

### No browser popup even though an alert shows in the Portfolio News page

You need: a supported browser, browser notification permission granted
(check your browser's site settings for the PWMS URL), the alert to be
newly created (Critical/High tier) — not one that already existed at your
last visit — and the Angular app open and polling (every 60 seconds).

### Too many Gemini rate-limit errors during a news monitor run

Increase the delay between calls in `backend/.env`:

```env
NEWS_MONITOR_AI_CALL_DELAY_SECONDS=6
```

Then restart the server (or re-run the command manually).

### I accidentally deleted `db.sqlite3`

This is your local development database — if it had real data, restore it
from a backup. If you intentionally want a fresh empty database:

```powershell
cd backend
python manage.py migrate
python manage.py createsuperuser
```

Then re-enter or re-import your data.

### A `mutual_funds` SIP test fails when I run the test suite

A few SIP-scheduling tests compare against today's real date and can drift
as time passes since they were written — this is a known, pre-existing
test-fixture limitation, not something this guide can fix for you. It does
not affect the running application, only that specific test file.

---

## Quick reference: full first-time setup, start to finish

```powershell
git clone https://github.com/avviiiral/Personal_Wealth_Monitoring.git
cd Personal_Wealth_Monitoring
git checkout Updates-2.0

cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

copy .env.example .env
# edit backend\.env — fill in GEMINI_API_KEY if you want AI Chat / News

python manage.py migrate
python manage.py check
python manage.py createsuperuser
python manage.py runserver
```

In a second terminal:

```powershell
cd Personal_Wealth_Monitoring\frontend
npm install
npm start
```

Open `http://localhost:4200/login` and sign in with the account you just
created.
