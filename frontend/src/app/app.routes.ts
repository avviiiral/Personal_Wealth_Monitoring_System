import { Routes } from '@angular/router';

import { ShellComponent } from './layout/shell/shell.component';

import { authGuard } from '../core/guards/auth.guard';

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () =>
      import('../features/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: '',
    component: ShellComponent,
    canActivate: [authGuard],
    children: [
      {
        path: '',
        redirectTo: 'dashboard',
        pathMatch: 'full',
      },
      {
        path: 'dashboard',
        loadComponent: () =>
          import('../features/dashboard/dashboard.component').then((m) => m.DashboardComponent),
      },
      {
        path: 'portfolio',
        loadComponent: () =>
          import('../features/portfolio/portfolio.component').then((m) => m.PortfolioComponent),
      },
      {
        path: 'reports',
        loadComponent: () =>
          import('../features/reports/reports.component').then((m) => m.ReportsComponent),
      },
      {
        path: 'reports/holdings',
        loadComponent: () =>
          import('../features/holding-reports/holding-reports.component').then((m) => m.HoldingReportsComponent),
      },
      {
        path: 'downloads',
        loadComponent: () =>
          import('../features/downloads/downloads.component').then((m) => m.DownloadsComponent),
      },
      {
        path: 'analytics',
        loadComponent: () =>
          import('../features/analytics/analytics.component').then((m) => m.AnalyticsComponent),
      },
      {
        path: 'settings',
        loadComponent: () =>
          import('../features/settings/settings.component').then((m) => m.SettingsComponent),
      },
      {
        path: 'ai-chat',
        loadComponent: () =>
          import('../features/ai-chat/ai-chat.component').then((m) => m.AiChatComponent),
      },
      {
        path: 'portfolio-news',
        loadComponent: () =>
          import('../features/portfolio-news/portfolio-news-list.component').then((m) => m.PortfolioNewsListComponent),
      },
      {
        path: 'portfolio-news/:id',
        loadComponent: () =>
          import('../features/portfolio-news/portfolio-news-detail.component').then((m) => m.PortfolioNewsDetailComponent),
      },
      {
        path: 'watch-list',
        loadComponent: () =>
          import('../features/watch-list/watch-list.component').then((m) => m.WatchListComponent),
      },
    ],
  },
  {
    path: '**',
    redirectTo: 'dashboard',
  },
];
