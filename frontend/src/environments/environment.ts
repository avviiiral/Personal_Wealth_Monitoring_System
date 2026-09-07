// Development environment - used by `ng serve` and plain `ng build`.
// This is the ONE place the backend's address is set for local dev;
// every API service imports apiUrl from here instead of hardcoding
// it, so changing where the backend runs means editing this file
// only, not 11 different service files.

export const environment = {
  production: false,
  apiUrl: 'http://localhost:8000',
};
