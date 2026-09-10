import { Component, inject } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';

import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent {
  private readonly fb = inject(FormBuilder);

  private readonly authService = inject(AuthService);

  private readonly router = inject(Router);

  loading = false;

  error = '';

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

    const { username, password } = this.loginForm.getRawValue();

    this.authService.login(username, password).subscribe({
      next: (response) => {
        this.loading = false;

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
}
