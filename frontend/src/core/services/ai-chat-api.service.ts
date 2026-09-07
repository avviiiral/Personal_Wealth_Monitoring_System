import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface AiChatResponse {
  answer?: string;
  error?: string;
  detail?: any;
}

@Injectable({
  providedIn: 'root',
})
export class AiChatApiService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/ai`;

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

  sendMessage(message: string): Observable<AiChatResponse> {
    const csrfToken = this.readCsrfToken();

    const headers = csrfToken
      ? new HttpHeaders({
          'X-CSRFToken': csrfToken,
          'Content-Type': 'application/json',
        })
      : undefined;

    return this.http.post<AiChatResponse>(
      `${this.baseUrl}/chat/`,
      {
        message,
      },
      {
        withCredentials: true,
        headers,
      },
    );
  }
}
