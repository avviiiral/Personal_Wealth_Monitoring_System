# Roles & Family Access

## Role hierarchy

```text
VIEWER < ADMIN < SUPER_USER < SYSTEM_OWNER
```

Role answers **what a user can do**. Family membership answers **whose data the user can see**. These are separate concepts.

| Capability | Viewer | Admin | Super User | System Owner |
|---|:---:|:---:|:---:|:---:|
| View Dashboard / Portfolio / Analytics / AI / News | ✅ | ✅ | ✅ | ✅ |
| Edit manual prices within visible family scope | ❌ | ✅ | ✅ | ✅ |
| Create Viewer | ❌ | ✅ | ✅ | ✅ |
| Create Admin | ❌ | ❌ | ✅ | ✅ |
| Create Super User / System Owner | ❌ | ❌ | ❌ | ✅ |
| Create/manage families | ❌ | ❌ | ❌ | ✅ |
| View every family's data | ❌ | ❌ | ❌ | ✅ |

## Family membership

A user may belong to zero, one or multiple families. The active-family selection scopes family-aware screens to the selected family. System Owner has global visibility.

## Enforcement model

Permissions are enforced on the backend. The frontend may hide buttons such as Edit, but hiding a button is not the security boundary. Server-side permission checks derive the authenticated user's role and visible owner/family scope from the database.

## Important implementation areas

- `backend/users/permissions.py` — centralized authorization helpers and permission classes
- `backend/users/` — user profile, FamilyGroup, FamilyMembership and audit-log models
- `frontend/src/core/services/rbac.service.ts` — frontend permission state used for UI decisions
- `frontend/src/core/guards/auth.guard.ts` — authentication route protection

## Security principle

Never trust a role, family ID or owner ID supplied by the browser. Backend checks must re-derive access from the authenticated session and stored memberships on every protected request.
