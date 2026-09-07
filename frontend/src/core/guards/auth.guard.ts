import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, map, of, switchMap } from 'rxjs';

import { AuthService } from '../services/auth.service';
import { RbacService } from '../services/rbac.service';

export const authGuard: CanActivateFn = () => {
  const authService = inject(AuthService);
  const rbacService = inject(RbacService);
  const router = inject(Router);

  const ensureRoleLoaded = () => {
    if (rbacService.isLoaded()) {
      return of(true);
    }

    return rbacService.load().pipe(
      map(() => true),

      catchError(() => {
        // Role lookup failing should not itself block navigation -
        // downstream UI simply treats an unloaded role as the most
        // restrictive (no admin controls shown), while every real
        // action still goes through the backend's own permission
        // checks regardless of what loaded here.
        return of(true);
      }),
    );
  };

  if (authService.isAuthenticated()) {
    return ensureRoleLoaded();
  }

  return authService.me().pipe(
    switchMap(() => ensureRoleLoaded()),

    catchError((error) => {
      console.log('User is not authenticated. Redirecting to login.', error.status);

      return of(router.createUrlTree(['/login']));
    }),
  );
};
