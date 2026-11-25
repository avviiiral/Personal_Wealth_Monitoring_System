# PWMS Frontend

The PWMS frontend is an Angular 21 standalone-component single-page application. It provides the authenticated user interface for portfolio management, analytics, mutual-fund intelligence, reports, watchlists, corporate actions, news and settings.

## Prerequisites

- Node.js supported by the Angular CLI version declared in `package.json` (Node 22 LTS is used in CI).
- npm version declared by the repository's `packageManager` field.
- The Django backend running on port 8000 for API requests.

## Install and run

From `frontend/`:

```powershell
npm ci
npm start
```

The development server runs at `http://localhost:4200/`. The committed development proxy forwards `/api` requests to Django. Start the backend separately from `backend/`:

```powershell
python manage.py runserver
```

The frontend uses relative `/api` URLs and session-cookie authentication. Backend write requests use Django's CSRF protection; the frontend services obtain the CSRF cookie from the health endpoint before sending mutations.

## Build and tests

```powershell
npm run build
npm run test -- --watch=false
```

The production build output is under `dist/frontend/browser/`. Unit tests run with Vitest through Angular's unit-test builder. CI runs a clean dependency install, production build and frontend unit tests.

## Application structure

- `src/app/`: root application configuration, route definitions, shell/layout and app-level features/services.
- `src/core/`: authentication, RBAC and shared API/state services.
- `src/features/`: screen-level standalone features such as Dashboard, Portfolio, Reports, Analytics, Watch List and MF Intelligence.
- `src/shared/`: reusable UI components.
- `src/environments/`: environment-specific API configuration.
- `public/push-sw.js`: browser push service worker.

Routes other than `/login` are protected by the authentication guard. The backend remains authoritative for role and family authorization; hiding a control in the frontend is not an access-control boundary.

## MF Intelligence theme behavior

The MF Intelligence feature uses Chart.js and observes the root document theme class. When the application changes between light and dark mode, its charts are refreshed so axes, labels, legends and chart surfaces use the active theme. Theme-specific badge and chart colors are maintained in the feature's component styles and chart configuration.

See [the MF Intelligence implementation notes](../docs/MF_INTELLIGENCE.md) for data sources, sync commands, shared consumers and calculation rules.

## Further documentation

- [Repository overview](../README.md)
- [Full setup guide](../SETUP.md)
- [Contributing and test expectations](../CONTRIBUTING.md)
