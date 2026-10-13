import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, switchMap } from 'rxjs';
import { environment } from '../../environments/environment';

/* ==========================================================
   TRANSACTION IMPORT
   Backend: POST /api/investments/import/
   (investments/urls.py -> investments/views.py::import_transactions)
   ========================================================== */

export interface DuplicateTransactionRow {
  row_number: number; asset_name: string; family_name: string; asset_class: string; sub_class: string; transaction_date: string; transaction_type: string; quantity: string; price: string; amount: string; reason: string;
}
export interface DuplicatePreviewResponse { success: boolean; message?: string; data?: { duplicate_rows: DuplicateTransactionRow[]; duplicate_count: number }; }

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

  previewTransactionDuplicates(file: File): Observable<DuplicatePreviewResponse> {
    const formData = new FormData(); formData.append('file', file);
    return this.getCsrfToken().pipe(switchMap(() => this.http.post<DuplicatePreviewResponse>(this.baseUrl + '/import/preview/', formData, { headers: this.getCsrfHeaders(), withCredentials: true })));
  }

  importTransactions(file: File, selectedDuplicateRows: number[] = []): Observable<ImportTransactionsResponse> {
    const formData = new FormData();

    formData.append('file', file);
    formData.append('selected_duplicate_rows', JSON.stringify(selectedDuplicateRows));

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
