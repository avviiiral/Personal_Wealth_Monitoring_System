import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';

import { provideHttpClient, withFetch, withXsrfConfiguration } from '@angular/common/http';

import { provideRouter, withPreloading, PreloadAllModules } from '@angular/router';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),

    // Keep the existing lazy routes and UI, but preload their chunks after
    // the first screen becomes interactive so navigation does not wait for
    // a fresh JavaScript chunk on every page change.
    provideRouter(routes, withPreloading(PreloadAllModules)),

    provideHttpClient(
      withFetch(),

      withXsrfConfiguration({
        cookieName: 'csrftoken',
        headerName: 'X-CSRFToken',
      }),
    ),
  ],
};
