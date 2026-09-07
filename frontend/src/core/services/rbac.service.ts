import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';

import { Observable, tap } from 'rxjs';
import { environment } from '../../environments/environment';

/**
 * PWMS role hierarchy (low -> high):
 *
 *   VIEWER < ADMIN < SUPERUSER (Super User) < SYSTEM_OWNER
 *
 * The wire value for "Super User" is intentionally still the
 * string "SUPERUSER" - it matches the backend's stored value
 * (see users.models.Role on the backend). Only the human label
 * changed from the old 3-role model, where SUPERUSER was the top
 * role; SYSTEM_OWNER is now the top role.
 */
export type PwmsRole = 'VIEWER' | 'ADMIN' | 'SUPERUSER' | 'SYSTEM_OWNER';

export const ROLE_LABELS: Record<PwmsRole, string> = {
  VIEWER: 'Viewer',
  ADMIN: 'Admin',
  SUPERUSER: 'Super User',
  SYSTEM_OWNER: 'System Owner',
};

// Ascending order of authority - mirrors users.models.ROLE_ORDER on
// the backend. Used only for display ordering; the backend is the
// sole source of truth for what any role may actually do.
export const ROLE_ORDER: PwmsRole[] = ['VIEWER', 'ADMIN', 'SUPERUSER', 'SYSTEM_OWNER'];

export interface FamilySummary {
  id: number;
  name: string;
}

export interface CurrentUserPermissions {
  can_edit_prices: boolean;

  can_manage_users: boolean;
  can_create_viewer: boolean;
  can_create_admin: boolean;
  can_create_super_user: boolean;
  can_create_system_owner: boolean;
  can_manage_viewer: boolean;
  can_manage_admin: boolean;
  can_manage_super_user: boolean;
  can_manage_system_owner: boolean;
  can_change_roles: boolean;
  assignable_roles: PwmsRole[];

  can_manage_families: boolean;
  can_view_all_families: boolean;
  can_assign_multiple_families: boolean;

  /** @deprecated kept for any lingering 3-role-era callers; use can_create_super_user. */
  can_assign_superuser: boolean;
}

export interface CurrentUser {
  id: number;
  first_name: string;
  last_name: string;
  username: string;
  email: string;
  role: PwmsRole;
  status: string;
  is_active: boolean;
  last_login: string | null;
  date_joined: string;
  families: FamilySummary[];
  active_family: FamilySummary | null;
  created_by: string | null;
  permissions: CurrentUserPermissions;
}

/**
 * Centralized authentication/role state for RBAC decisions in the
 * UI. This is display/navigation logic ONLY - every action is
 * still independently authorized by the Django backend, which is
 * the single source of truth. Hiding a control here never
 * substitutes for a server-side permission check.
 */
@Injectable({
  providedIn: 'root',
})
export class RbacService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/settings`;

  private readonly currentUserSignal = signal<CurrentUser | null>(null);

  readonly currentUser = this.currentUserSignal.asReadonly();

  private loaded = false;

  // ======================================================
  // CSRF (needed for the active-family POST below)
  // ======================================================

  private readCsrfToken(): string | null {
    const cookies = document.cookie.split(';');

    for (const cookie of cookies) {
      const [name, ...valueParts] = cookie.trim().split('=');

      if (name === 'csrftoken') {
        return decodeURIComponent(valueParts.join('='));
      }
    }

    return null;
  }

  private writeHeaders(): { withCredentials: true; headers?: HttpHeaders } {
    const csrfToken = this.readCsrfToken();

    return {
      withCredentials: true,
      headers: csrfToken
        ? new HttpHeaders({ 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' })
        : undefined,
    };
  }

  // ======================================================
  // LOAD
  // ======================================================

  load(): Observable<CurrentUser> {
    return this.http.get<CurrentUser>(`${this.baseUrl}/me/`, { withCredentials: true }).pipe(
      tap((user) => {
        this.currentUserSignal.set(user);
        this.loaded = true;
      }),
    );
  }

  clear(): void {
    this.currentUserSignal.set(null);
    this.loaded = false;
  }

  /**
   * Populate the RBAC state directly from a CurrentUser payload
   * already fetched elsewhere (e.g. AuthService's login/me calls,
   * which return the same shape) - avoids a redundant round trip
   * to `/api/settings/me/` right after login.
   */
  hydrate(user: CurrentUser): void {
    this.currentUserSignal.set(user);
    this.loaded = true;
  }

  isLoaded(): boolean {
    return this.loaded;
  }

  // ======================================================
  // ACTIVE FAMILY (personal view preference, any role)
  // ======================================================

  setActiveFamily(familyId: number | null): Observable<CurrentUser> {
    return this.http
      .post<CurrentUser>(
        `${this.baseUrl}/me/active-family/`,
        { family_id: familyId },
        this.writeHeaders(),
      )
      .pipe(tap((user) => this.currentUserSignal.set(user)));
  }

  // ======================================================
  // ROLE CHECKS
  // ======================================================

  role(): PwmsRole | null {
    return this.currentUserSignal()?.role ?? null;
  }

  roleLabel(): string {
    const role = this.role();
    return role ? ROLE_LABELS[role] : '-';
  }

  isViewer(): boolean {
    return this.role() === 'VIEWER';
  }

  isAdmin(): boolean {
    return this.role() === 'ADMIN';
  }

  isSuperUser(): boolean {
    return this.role() === 'SUPERUSER';
  }

  isSystemOwner(): boolean {
    return this.role() === 'SYSTEM_OWNER';
  }

  // ======================================================
  // FAMILIES
  // ======================================================

  families(): FamilySummary[] {
    return this.currentUserSignal()?.families ?? [];
  }

  hasMultipleFamilies(): boolean {
    return this.families().length > 1;
  }

  activeFamily(): FamilySummary | null {
    return this.currentUserSignal()?.active_family ?? null;
  }

  // ======================================================
  // PERMISSIONS (mirrors the backend permission matrix - see
  // users/permissions.py. Display/navigation only.)
  // ======================================================

  private perm(): CurrentUserPermissions | undefined {
    return this.currentUserSignal()?.permissions;
  }

  canEditPrices(): boolean {
    return !!this.perm()?.can_edit_prices;
  }

  canManageUsers(): boolean {
    return !!this.perm()?.can_manage_users;
  }

  canCreateViewer(): boolean {
    return !!this.perm()?.can_create_viewer;
  }

  canCreateAdmin(): boolean {
    return !!this.perm()?.can_create_admin;
  }

  canCreateSuperUser(): boolean {
    return !!this.perm()?.can_create_super_user;
  }

  canCreateSystemOwner(): boolean {
    return !!this.perm()?.can_create_system_owner;
  }

  canManageViewer(): boolean {
    return !!this.perm()?.can_manage_viewer;
  }

  canManageAdmin(): boolean {
    return !!this.perm()?.can_manage_admin;
  }

  canManageSuperUser(): boolean {
    return !!this.perm()?.can_manage_super_user;
  }

  canManageSystemOwner(): boolean {
    return !!this.perm()?.can_manage_system_owner;
  }

  canChangeRoles(): boolean {
    return !!this.perm()?.can_change_roles;
  }

  assignableRoles(): PwmsRole[] {
    return this.perm()?.assignable_roles ?? [];
  }

  canManageFamilies(): boolean {
    return !!this.perm()?.can_manage_families;
  }

  canViewAllFamilies(): boolean {
    return !!this.perm()?.can_view_all_families;
  }

  canAssignMultipleFamilies(): boolean {
    return !!this.perm()?.can_assign_multiple_families;
  }

  /** @deprecated use canCreateSuperUser() */
  canAssignSuperUser(): boolean {
    return !!this.perm()?.can_assign_superuser;
  }
}
