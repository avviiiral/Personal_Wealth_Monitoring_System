# Deployment & Operations

## Development

Backend:

```powershell
cd backend
python manage.py migrate
python manage.py runserver
```

Frontend:

```powershell
cd frontend
npm install
ng serve
```

Use the repository's current `SETUP.md` for the complete environment setup and dependency installation procedure.

## Configuration

Deployment-sensitive settings are environment-driven. Keep real secrets out of source control. Use the provided environment template and configure the backend secret key, debug mode, allowed hosts, CORS and external AI/data credentials for the deployment environment.

## Database

The project currently uses SQLite by default. The repository configures SQLite for development and concurrent background activity. For production, evaluate database choice, backups, file locking and operational concurrency for the intended workload before exposing the system publicly.

## Serving

Development uses Django's development server. The repository also supports production-oriented serving through Waitress (WSGI) or Uvicorn (ASGI), depending on the deployment mode and features required.

## Background processing

PWMS currently uses in-process Python threads for scheduled work such as market refreshes and portfolio-news processing. There is no required Celery/Redis stack in the documented architecture.

## Frontend deployment

The Angular environment configuration is the main place where the backend API URL is selected for browser requests. Production builds use Angular's environment replacement mechanism as configured in `angular.json`.

## Secrets

Never commit `.env`, API keys, Gemini keys, passwords, Django secret keys or other deployment credentials. Use environment variables or the deployment platform's secret store.

## Validation before release

Run the backend Django system check and the project's Django test suite from `backend`. Build the Angular frontend separately and verify that the production environment points to the intended backend URL.
