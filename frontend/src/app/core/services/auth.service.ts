import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';

import { Observable, BehaviorSubject, tap, catchError, of, switchMap } from 'rxjs';

export interface AuthUser {
  id: number;
  username: string;
  email: string;
}

export interface LoginResponse {
  authenticated: boolean;
  user: AuthUser | null;
}

@Injectable({
  providedIn: 'root',
})
export class AuthService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = 'http://localhost:8000/api/auth';

  /*
   * Django's existing health endpoint uses
   * @ensure_csrf_cookie, so calling it gives the
   * browser the csrftoken cookie.
   */
  private readonly csrfUrl = 'http://localhost:8000/api/health/';

  private readonly authenticatedSubject = new BehaviorSubject<boolean>(false);

  readonly authenticated$ = this.authenticatedSubject.asObservable();

  private readonly userSubject = new BehaviorSubject<AuthUser | null>(null);

  readonly user$ = this.userSubject.asObservable();

  private authenticationChecked = false;

  // ==========================================================
  // CSRF TOKEN
  // ==========================================================

  private getCsrfToken(): Observable<any> {
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

    return headers;
  }

  // ==========================================================
  // LOGIN
  // ==========================================================

  login(username: string, password: string): Observable<LoginResponse> {
    /*
     * Get the Django CSRF cookie before POST /login/.
     */
    return this.getCsrfToken().pipe(
      switchMap(() => {
        return this.http.post<LoginResponse>(
          `${this.baseUrl}/login/`,
          {
            username,
            password,
          },
          {
            headers: this.getCsrfHeaders(),
            withCredentials: true,
          },
        );
      }),

      tap((response) => {
        this.authenticatedSubject.next(response.authenticated);

        this.userSubject.next(response.user);

        this.authenticationChecked = true;
      }),
    );
  }

  // ==========================================================
  // CURRENT USER
  // ==========================================================

  me(): Observable<LoginResponse> {
    if (this.authenticationChecked) {
      return of({
        authenticated: this.authenticatedSubject.value,

        user: this.userSubject.value,
      });
    }

    return this.http
      .get<LoginResponse>(`${this.baseUrl}/me/`, {
        withCredentials: true,
      })
      .pipe(
        tap((response) => {
          this.authenticatedSubject.next(response.authenticated);

          this.userSubject.next(response.user);

          this.authenticationChecked = true;
        }),

        catchError((error) => {
          this.authenticatedSubject.next(false);

          this.userSubject.next(null);

          this.authenticationChecked = true;

          throw error;
        }),
      );
  }

  // ==========================================================
  // LOGOUT
  // ==========================================================

  logout(): Observable<any> {
    return this.getCsrfToken().pipe(
      switchMap(() => {
        return this.http.post(
          `${this.baseUrl}/logout/`,
          {},
          {
            headers: this.getCsrfHeaders(),
            withCredentials: true,
          },
        );
      }),

      tap(() => {
        this.clearAuthenticationState();
      }),

      catchError((error) => {
        /*
         * Clear frontend authentication state even if
         * the backend logout request fails.
         */
        this.clearAuthenticationState();

        throw error;
      }),
    );
  }

  // ==========================================================
  // CLEAR AUTH STATE
  // ==========================================================

  private clearAuthenticationState(): void {
    this.authenticatedSubject.next(false);

    this.userSubject.next(null);

    this.authenticationChecked = true;
  }

  // ==========================================================
  // STATE
  // ==========================================================

  isAuthenticated(): boolean {
    return this.authenticatedSubject.value;
  }

  get currentUser(): AuthUser | null {
    return this.userSubject.value;
  }
}
