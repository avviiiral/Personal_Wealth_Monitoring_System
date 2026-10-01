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

export interface TaxRateSetting {
  id: number | null;
  asset_id: number;
  asset_name: string;
  family_id: number;
  family_name: string;
  tenure_months: number | null;
  short_term_tax_rate: string | number | null;
  long_term_tax_rate: string | number | null;
  updated_at: string | null;
}
export interface TaxRateChangeLog {
  id: number;
  user: string;
  date_time: string;
  asset_name: string;
  change_from: {
    tenure_months: number | null;
    short_term_tax_rate: string | null;
    long_term_tax_rate: string | null;
  };
  change_to: {
    tenure_months: number | null;
    short_term_tax_rate: string | null;
    long_term_tax_rate: string | null;
  };
}

export interface ChangePasswordResponse {
  message: string;
}

export interface TransactionEditHistory {
  id: number;
  transaction_id: number | null;
  edited_by_username: string;
  edited_at: string;
  asset_name: string;
  old_values: Record<string, string | number | null>;
  new_values: Record<string, string | number | null>;
  changed_fields: string[];
}

export interface TransactionEditHistoryResponse {
  count: number;
  results: TransactionEditHistory[];
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
  private readonly requestOptions = { withCredentials: true };

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

  getSettings(): Observable<SettingsResponse> {
    return this.http.get<SettingsResponse>(`${this.baseUrl}/settings/`, this.requestOptions);
  }


  getTaxRateSettings(): Observable<TaxRateSetting[]> {
    return this.http.get<TaxRateSetting[]>(`${this.baseUrl}/settings/tax-rates/`, this.requestOptions);
  }

  saveTaxRateSetting(
    assetId: number,
    tenureMonths: number,
    shortTermTaxRate: number,
    longTermTaxRate: number,
  ): Observable<TaxRateSetting> {
    const csrfToken = this.readCsrfToken();
    const headers = csrfToken
      ? new HttpHeaders({ 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' })
      : undefined;

    return this.http.post<TaxRateSetting>(
      `${this.baseUrl}/settings/tax-rates/`,
      {
        asset_id: assetId,
        tenure_months: tenureMonths,
        short_term_tax_rate: shortTermTaxRate,
        long_term_tax_rate: longTermTaxRate,
      },
      { withCredentials: true, headers },
    );
  }

  updateTaxRateSetting(
    id: number,
    tenureMonths: number,
    shortTermTaxRate: number,
    longTermTaxRate: number,
  ): Observable<TaxRateSetting> {
    const csrfToken = this.readCsrfToken();
    const headers = csrfToken
      ? new HttpHeaders({ 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' })
      : undefined;

    return this.http.patch<TaxRateSetting>(
      `${this.baseUrl}/settings/tax-rates/${id}/`,
      {
        tenure_months: tenureMonths,
        short_term_tax_rate: shortTermTaxRate,
        long_term_tax_rate: longTermTaxRate,
      },
      { withCredentials: true, headers },
    );
  }

  deleteTaxRateSetting(id: number): Observable<{ message: string }> {
    const csrfToken = this.readCsrfToken();
    const headers = csrfToken
      ? new HttpHeaders({ 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' })
      : undefined;

    return this.http.delete<{ message: string }>(
      `${this.baseUrl}/settings/tax-rates/${id}/`,
      { withCredentials: true, headers },
    );
  }

  getTaxRateChangeHistory(): Observable<TaxRateChangeLog[]> {
    return this.http.get<TaxRateChangeLog[]>(
      `${this.baseUrl}/settings/tax-rates/history/`,
      this.requestOptions,
    );
  }

  getTransactionEditHistory(): Observable<TransactionEditHistoryResponse> {
    return this.http.get<TransactionEditHistoryResponse>(
      `${environment.apiUrl}/api/portfolio/transactions/edit-history/`,
      this.requestOptions,
    );
  }

  updateSettings(
    data: Partial<SettingsPreferences> & { email?: string },
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
