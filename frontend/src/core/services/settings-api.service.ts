import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';

import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface SettingsProfile {
  id: number;
  username: string;
  email: string;
}

export interface SettingsPreferences {
  currency: string;
  date_format: string;
  default_analytics_period: number;
}

export interface SettingsResponse {
  profile: SettingsProfile;
  preferences: SettingsPreferences;
}

export interface UpdateSettingsResponse extends SettingsResponse {
  message: string;
}

export interface ChangePasswordResponse {
  message: string;
}

interface CsrfResponse {
  csrfToken?: string;
}

@Injectable({
  providedIn: 'root',
})
export class SettingsApiService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api`;

  private readonly requestOptions = {
    withCredentials: true,
  };

  // ======================================================
  // CSRF
  // ======================================================

  private getCsrfToken(): Observable<CsrfResponse> {
    return this.http.get<CsrfResponse>(`${this.baseUrl}/health/`, {
      withCredentials: true,
    });
  }

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

  // ======================================================
  // SETTINGS
  // ======================================================

  getSettings(): Observable<SettingsResponse> {
    return this.http.get<SettingsResponse>(`${this.baseUrl}/settings/`, this.requestOptions);
  }

  updateSettings(
    data: Partial<SettingsPreferences> & {
      email?: string;
    },
  ): Observable<UpdateSettingsResponse> {
    const csrfToken = this.readCsrfToken();

    const headers = csrfToken
      ? new HttpHeaders({
          'X-CSRFToken': csrfToken,
          'Content-Type': 'application/json',
        })
      : undefined;

    return this.http.patch<UpdateSettingsResponse>(`${this.baseUrl}/settings/update/`, data, {
      withCredentials: true,
      headers,
    });
  }

  changePassword(
    currentPassword: string,
    newPassword: string,
    confirmPassword: string,
  ): Observable<ChangePasswordResponse> {
    const csrfToken = this.readCsrfToken();

    const headers = csrfToken
      ? new HttpHeaders({
          'X-CSRFToken': csrfToken,
          'Content-Type': 'application/json',
        })
      : undefined;

    return this.http.post<ChangePasswordResponse>(
      `${this.baseUrl}/settings/change-password/`,
      {
        current_password: currentPassword,

        new_password: newPassword,

        confirm_password: confirmPassword,
      },
      {
        withCredentials: true,
        headers,
      },
    );
  }
}
