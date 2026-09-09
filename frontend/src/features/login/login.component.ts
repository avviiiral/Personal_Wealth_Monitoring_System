import { Component, inject } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [ReactiveFormsModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent {
  private readonly fb = inject(FormBuilder);

  private readonly authService = inject(AuthService);

  private readonly router = inject(Router);

  loading = false;

  error = '';

  // ==========================================================
  // TWO-FACTOR AUTHENTICATION
  // ==========================================================

  requiresTwoFactor = false;

  twoFactorUserId: number | null = null;

  twoFactorCode = '';

  twoFactorError = '';

  verifyingTwoFactor = false;

  readonly loginForm = this.fb.nonNullable.group({
    username: ['', Validators.required],

    password: ['', Validators.required],
  });

  // ==========================================================
  // LOGIN
  // ==========================================================

  submit(): void {
    if (this.loginForm.invalid) {
      this.loginForm.markAllAsTouched();

      return;
    }

    this.loading = true;

    this.error = '';

    this.twoFactorError = '';

    const { username, password } = this.loginForm.getRawValue();

    this.authService.login(username, password).subscribe({
      next: (response) => {
        this.loading = false;

        // ----------------------------------------------------
        // TWO-FACTOR AUTHENTICATION REQUIRED
        // ----------------------------------------------------

        if (response.requires_2fa) {
          this.requiresTwoFactor = true;

          this.twoFactorUserId = response.user_id ?? null;

          this.twoFactorCode = '';

          return;
        }

        // ----------------------------------------------------
        // NORMAL LOGIN
        // ----------------------------------------------------

        if (response.authenticated) {
          this.router.navigate(['/dashboard']);
        }
      },

      error: (error) => {
        this.loading = false;

        if (error.status === 401) {
          this.error = 'Invalid username/email or password.';
        } else if (error.status === 0) {
          this.error = 'Unable to connect to the PWMS backend.';
        } else {
          this.error = 'Login failed. Please try again.';
        }

        console.error('Login error:', error);
      },
    });
  }

  // ==========================================================
  // VERIFY TWO-FACTOR AUTHENTICATION
  // ==========================================================

  verifyTwoFactor(): void {
    if (this.verifyingTwoFactor) {
      return;
    }

    this.twoFactorError = '';

    if (!this.twoFactorUserId) {
      this.twoFactorError = 'Invalid two-factor authentication request.';

      return;
    }

    const code = this.twoFactorCode.trim();

    if (!/^\d{6}$/.test(code)) {
      this.twoFactorError = 'Enter the 6-digit authentication code.';

      return;
    }

    this.verifyingTwoFactor = true;

    this.authService.verifyTwoFactor(this.twoFactorUserId, code).subscribe({
      next: (response) => {
        this.verifyingTwoFactor = false;

        if (response.authenticated) {
          this.requiresTwoFactor = false;

          this.twoFactorUserId = null;

          this.twoFactorCode = '';

          this.router.navigate(['/dashboard']);
        }
      },

      error: (error) => {
        this.verifyingTwoFactor = false;

        this.twoFactorError = error?.error?.detail || 'Invalid authentication code.';

        console.error('Two-factor verification error:', error);
      },
    });
  }

  // ==========================================================
  // BACK TO PASSWORD LOGIN
  // ==========================================================

  backToLogin(): void {
    this.requiresTwoFactor = false;

    this.twoFactorUserId = null;

    this.twoFactorCode = '';

    this.twoFactorError = '';

    this.error = '';
  }
}
