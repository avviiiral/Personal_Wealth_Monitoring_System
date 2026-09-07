import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';

import { Observable } from 'rxjs';

import { PwmsRole, FamilySummary } from './rbac.service';
import { environment } from '../../environments/environment';

export interface ManagedUser {
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
}

export interface CreateUserPayload {
  first_name?: string;
  last_name?: string;
  username: string;
  email: string;
  password: string;
  confirm_password: string;
  role: PwmsRole;
  is_active?: boolean;
  /** System Owner only - every other role must omit this entirely. */
  family_ids?: number[];
}

export interface UpdateUserPayload {
  first_name?: string;
  last_name?: string;
  username?: string;
  email?: string;
  role?: PwmsRole;
  is_active?: boolean;
  /** System Owner only - every other role must omit this entirely. */
  family_ids?: number[];
}

export interface FamilyGroupMember {
  id: number;
  first_name: string;
  last_name: string;
  username: string;
  email: string;
  role: PwmsRole;
}

export interface FamilyGroup {
  id: number;
  name: string;
  created_by_username: string | null;
  created_at: string;
  members: FamilyGroupMember[];
}

export interface ApiErrorDetail {
  detail: string | Record<string, string[] | string>;
}

@Injectable({
  providedIn: 'root',
})
export class UserManagementApiService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/settings`;

  private readonly requestOptions = {
    withCredentials: true,
  };

  // ======================================================
  // CSRF
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
        ? new HttpHeaders({
            'X-CSRFToken': csrfToken,
            'Content-Type': 'application/json',
          })
        : undefined,
    };
  }

  // ======================================================
  // LIST / CREATE
  // ======================================================

  listUsers(): Observable<ManagedUser[]> {
    return this.http.get<ManagedUser[]>(`${this.baseUrl}/users/`, this.requestOptions);
  }

  createUser(payload: CreateUserPayload): Observable<ManagedUser> {
    return this.http.post<ManagedUser>(`${this.baseUrl}/users/`, payload, this.writeHeaders());
  }

  // ======================================================
  // DETAIL / UPDATE
  // ======================================================

  getUser(id: number): Observable<ManagedUser> {
    return this.http.get<ManagedUser>(`${this.baseUrl}/users/${id}/`, this.requestOptions);
  }

  updateUser(id: number, payload: UpdateUserPayload): Observable<ManagedUser> {
    return this.http.patch<ManagedUser>(
      `${this.baseUrl}/users/${id}/`,
      payload,
      this.writeHeaders(),
    );
  }

  // ======================================================
  // ACTIVATE / DEACTIVATE
  // ======================================================

  activateUser(id: number): Observable<ManagedUser> {
    return this.http.post<ManagedUser>(
      `${this.baseUrl}/users/${id}/activate/`,
      {},
      this.writeHeaders(),
    );
  }

  deactivateUser(id: number): Observable<ManagedUser> {
    return this.http.post<ManagedUser>(
      `${this.baseUrl}/users/${id}/deactivate/`,
      {},
      this.writeHeaders(),
    );
  }

  // ======================================================
  // RESET PASSWORD
  // ======================================================

  resetPassword(
    id: number,
    newPassword: string,
    confirmPassword: string,
  ): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(
      `${this.baseUrl}/users/${id}/reset-password/`,
      {
        new_password: newPassword,
        confirm_password: confirmPassword,
      },
      this.writeHeaders(),
    );
  }

  // ======================================================
  // DELETE
  // ======================================================

  deleteUser(id: number): Observable<{ message: string }> {
    return this.http.delete<{ message: string }>(
      `${this.baseUrl}/users/${id}/`,
      this.writeHeaders(),
    );
  }

  // ======================================================
  // FAMILY GROUPS
  // ======================================================

  listGroups(): Observable<FamilyGroup[]> {
    return this.http.get<FamilyGroup[]>(`${this.baseUrl}/groups/`, this.requestOptions);
  }

  createGroup(name: string): Observable<FamilyGroup> {
    return this.http.post<FamilyGroup>(
      `${this.baseUrl}/groups/`,
      { name },
      this.writeHeaders(),
    );
  }

  renameGroup(id: number, name: string): Observable<FamilyGroup> {
    return this.http.patch<FamilyGroup>(
      `${this.baseUrl}/groups/${id}/`,
      { name },
      this.writeHeaders(),
    );
  }

  deleteGroup(id: number): Observable<{ message: string }> {
    return this.http.delete<{ message: string }>(
      `${this.baseUrl}/groups/${id}/`,
      this.writeHeaders(),
    );
  }

  addGroupMember(groupId: number, userId: number): Observable<FamilyGroup> {
    return this.http.post<FamilyGroup>(
      `${this.baseUrl}/groups/${groupId}/members/`,
      { user_id: userId },
      this.writeHeaders(),
    );
  }

  removeGroupMember(groupId: number, userId: number): Observable<FamilyGroup> {
    return this.http.delete<FamilyGroup>(
      `${this.baseUrl}/groups/${groupId}/members/${userId}/`,
      this.writeHeaders(),
    );
  }
}
