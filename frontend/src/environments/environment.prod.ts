// Production environment - swapped in for environment.ts automatically
// by `ng build --configuration production` (see angular.json's
// fileReplacements). Edit apiUrl here - and nowhere else - before
// building for a real deployment.
//
// Production uses same-origin /api routing by default; place the backend behind the same origin.
// (e.g. nginx reverse-proxying /api to Django on the same domain),
// set this to '' (empty string) so requests go to a relative path
// instead of a hardcoded absolute URL.

export const environment = {
  production: true,
  apiUrl: '',
};
