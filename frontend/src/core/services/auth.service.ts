import { Injectable, inject } from '@angular/core';

import { HttpClient, HttpHeaders } from '@angular/common/http';

import { Observable, BehaviorSubject, tap, catchError, of, switchMap } from 'rxjs';

import { RbacService, CurrentUser } from './rbac.service';

import { environment } from '../../environments/environment';

/**
 * The authenticated user's identity AND role/permission/family
 * state, as returned by `/api/auth/login/` and `/api/auth/me/`.
 */
export type AuthUser = CurrentUser;

export interface LoginResponse {
  authenticated: boolean;
  user?: AuthUser;
  detail?: string;
}

@Injectable({
  providedIn: 'root',
})
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly rbacService = inject(RbacService);
  private readonly baseUrl = `${environment.apiUrl}/api/auth`;
  private readonly csrfUrl = `${environment.apiUrl}/api/health/`;

  private readonly authenticatedSubject = new BehaviorSubject<boolean>(false);
  readonly authenticated$ = this.authenticatedSubject.asObservable();

  private readonly userSubject = new BehaviorSubject<AuthUser | null>(null);
  readonly user$ = this.userSubject.asObservable();

  private authenticationChecked = false;

  private getCsrfToken(): Observable<unknown> {
    return this.http.get(this.csrfUrl, { withCredentials: true });
  }

  private readCsrfToken(): string {
    const cookie = document.cookie
      .split(';')
      .map((item) => item.trim())
      .find((item) => item.startsWith('csrftoken='));

    return cookie
      ? decodeURIComponent(cookie.substring('csrftoken='.length))
      : '';
  }

  private getCsrfHeaders(): HttpHeaders {
    const token = this.readCsrfToken();
    return token
      ? new HttpHeaders({ 'X-CSRFToken': token })
      : new HttpHeaders();
  }

  login(username: string, password: string): Observable<LoginResponse> {
    return this.getCsrfToken().pipe(
      switchMap(() =>
        this.http.post<LoginResponse>(
          `${this.baseUrl}/login/`,
          { username, password },
          {
            headers: this.getCsrfHeaders(),
            withCredentials: true,
          },
        ),
      ),
      tap((response) => {
        this.authenticatedSubject.next(response.authenticated);
        this.userSubject.next(response.user ?? null);
        this.authenticationChecked = true;

        if (response.user) {
          this.rbacService.hydrate(response.user);
        }
      }),
    );
  }

  me(): Observable<LoginResponse> {
    if (this.authenticationChecked) {
      if (this.authenticatedSubject.value) {
        return of({
          authenticated: true,
          user: this.userSubject.value!,
        });
      }

      return of({
        authenticated: false,
        user: undefined,
      });
    }

    return this.http
      .get<LoginResponse>(`${this.baseUrl}/me/`, {
        withCredentials: true,
      })
      .pipe(
        tap((response) => {
          this.authenticatedSubject.next(response.authenticated);
          this.userSubject.next(response.user ?? null);
          this.authenticationChecked = true;

          if (response.user) {
            this.rbacService.hydrate(response.user);
          }
        }),
        catchError((error) => {
          this.authenticatedSubject.next(false);
          this.userSubject.next(null);
          this.authenticationChecked = true;
          throw error;
        }),
      );
  }

  logout(): Observable<unknown> {
    return this.getCsrfToken().pipe(
      switchMap(() =>
        this.http.post(
          `${this.baseUrl}/logout/`,
          {},
          {
            headers: this.getCsrfHeaders(),
            withCredentials: true,
          },
        ),
      ),
      tap(() => {
        this.authenticatedSubject.next(false);
        this.userSubject.next(null);
        this.authenticationChecked = true;
        this.rbacService.clear();
      }),
      catchError((error) => {
        this.authenticatedSubject.next(false);
        this.userSubject.next(null);
        this.authenticationChecked = true;
        this.rbacService.clear();
        throw error;
      }),
    );
  }

  isAuthenticated(): boolean {
    return this.authenticatedSubject.value;
  }

  get currentUser(): AuthUser | null {
    return this.userSubject.value;
  }
}
