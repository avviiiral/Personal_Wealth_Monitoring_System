import { Routes } from '@angular/router';

import { ShellComponent } from './layout/shell/shell.component';

import { DashboardComponent } from '../features/dashboard/dashboard.component';
import { LoginComponent } from '../features/login/login.component';
import { PortfolioComponent } from '../features/portfolio/portfolio.component';
import { ReportsComponent } from '../features/reports/reports.component';

import { AnalyticsComponent } from '../features/analytics/analytics.component';
import { SettingsComponent } from '../features/settings/settings.component';
import { AiChatComponent } from '../features/ai-chat/ai-chat.component';
import { PortfolioNewsListComponent } from '../features/portfolio-news/portfolio-news-list.component';
import { PortfolioNewsDetailComponent } from '../features/portfolio-news/portfolio-news-detail.component';

import { PagePlaceholderComponent } from '../shared/components/page-placeholder.component';

import { authGuard } from '../core/guards/auth.guard';

export const routes: Routes = [
  // ----------------------------------------------------------
  // Public routes
  // ----------------------------------------------------------

  {
    path: 'login',
    component: LoginComponent,
  },

  // ----------------------------------------------------------
  // Protected PWMS application
  // ----------------------------------------------------------

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
        component: DashboardComponent,
      },

      {
        path: 'portfolio',
        component: PortfolioComponent,
      },

      {
        path: 'reports',
        component: ReportsComponent,
      },

      {
        path: 'analytics',
        component: AnalyticsComponent,
      },

      {
        path: 'settings',
        component: SettingsComponent,
      },

      {
        path: 'ai-chat',
        component: AiChatComponent,
      },

      {
        path: 'portfolio-news',
        component: PortfolioNewsListComponent,
      },

      {
        path: 'portfolio-news/:id',
        component: PortfolioNewsDetailComponent,
      },
    ],
  },

  // ----------------------------------------------------------
  // Unknown route
  // ----------------------------------------------------------

  {
    path: '**',
    redirectTo: 'dashboard',
  },
];
