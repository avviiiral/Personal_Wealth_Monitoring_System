import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, switchMap } from 'rxjs';
import { environment } from '../../environments/environment';

/* ==========================================================
   TRANSACTION IMPORT
   Backend: POST /api/investments/import/
   (investments/urls.py -> investments/views.py::import_transactions)
   ========================================================== */

export interface ImportTransactionsResult {
  imported_investments: number;
  imported_mutual_funds: number;
  skipped_duplicates: number;
  total_imported: number;
}

export interface ImportTransactionsResponse {
  success: boolean;
  message: string;
  data?: ImportTransactionsResult;
  error?: string;
}

@Injectable({
  providedIn: 'root',
})
export class InvestmentsApiService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/investments`;

  private readonly csrfUrl = `${environment.apiUrl}/api/health/`;

  // ==========================================================
  // CSRF TOKEN
  // (same pattern as PortfolioApiService / ManualPriceService)
  // ==========================================================

  private getCsrfToken(): Observable<unknown> {
    return this.http.get(this.csrfUrl, {
      withCredentials: true,
    });
  }

  private readCsrfToken(): string {
    const cookies = document.cookie.split(';');

    for (const cookie of cookies) {
      const trimmedCookie = cookie.trim();

      if (trimmedCookie.startsWith('csrftoken=')) {
        return decodeURIComponent(trimmedCookie.substring('csrftoken='.length));
      }
    }

    return '';
  }

  private getCsrfHeaders(): HttpHeaders {
    const csrfToken = this.readCsrfToken();

    let headers = new HttpHeaders();

    if (csrfToken) {
      headers = headers.set('X-CSRFToken', csrfToken);
    }

    // Deliberately not setting Content-Type here: the browser
    // must set it (including the multipart boundary) when the
    // request body is a FormData instance.

    return headers;
  }

  // ==========================================================
  // IMPORT TRANSACTIONS (.xlsx / .csv workbook upload)
  // ==========================================================

  importTransactions(file: File): Observable<ImportTransactionsResponse> {
    const formData = new FormData();

    formData.append('file', file);

    return this.getCsrfToken().pipe(
      switchMap(() =>
        this.http.post<ImportTransactionsResponse>(`${this.baseUrl}/import/`, formData, {
          headers: this.getCsrfHeaders(),
          withCredentials: true,
        }),
      ),
    );
  }
}
