import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';

import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../core/services/auth.service';
import { RbacService } from '../../core/services/rbac.service';

import {
  SettingsApiService,
  SettingsProfile,
  SettingsPreferences,
  TransactionEditHistory,
} from '../../core/services/settings-api.service';

import { UserManagementComponent } from './user-management/user-management.component';
import { FamilyManagementComponent } from './family-management/family-management.component';
import { ManualPricesComponent } from './manual-prices/manual-prices.component';

type SettingsTab =
  | 'account'
  | 'preferences'
  | 'security'
  | 'users'
  | 'families'
  | 'prices'
  | 'transaction-history';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    UserManagementComponent,
    FamilyManagementComponent,
    ManualPricesComponent,
  ],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit {
  private readonly settingsApi = inject(SettingsApiService);
  private readonly authService = inject(AuthService);
  readonly rbac = inject(RbacService);
  private readonly router = inject(Router);
  private readonly cdr = inject(ChangeDetectorRef);

  activeTab: SettingsTab = 'account';

  profile: SettingsProfile | null = null;

  preferences: SettingsPreferences = {
    currency: 'INR',
    date_format: 'DD MMM YYYY',
    default_analytics_period: 30,
  };

  email = '';
  currentPassword = '';
  newPassword = '';
  confirmPassword = '';

  loading = true;
  saving = false;
  changingPassword = false;
  loggingOut = false;

  error = '';
  profileMessage = '';
  passwordMessage = '';
  passwordError = '';

  transactionHistory: TransactionEditHistory[] = [];
  transactionHistoryLoading = false;
  transactionHistoryError = '';
  expandedHistoryId: number | null = null;

  historySearch = '';
  historyEditorFilter = '';
  historyDateFilter = '';
  historyPage = 1;
  historyPageSize = 10;

  ngOnInit(): void {
    this.loadSettings();

    if (!this.rbac.isLoaded()) {
      this.rbac.load().subscribe({
        next: () => this.cdr.detectChanges(),
        error: () => this.cdr.detectChanges(),
      });
    }
  }

  setTab(tab: SettingsTab): void {
    this.activeTab = tab;

    if (tab === 'transaction-history' && !this.transactionHistory.length) {
      this.loadTransactionHistory();
    }
  }

  canManageUsers(): boolean {
    return this.rbac.canManageUsers();
  }

  canManageFamilies(): boolean {
    return this.rbac.canManageFamilies();
  }

  canEditPrices(): boolean {
    return this.rbac.canEditPrices();
  }

  familyNamesText(): string {
    return this.rbac.families().map((f) => f.name).join(', ');
  }

  onActiveFamilyChange(familyId: number): void {
    this.rbac.setActiveFamily(familyId).subscribe({
      next: () => {
        this.profileMessage = 'Now viewing data for the selected family.';
        this.cdr.detectChanges();
      },
      error: (error) => {
        this.error = error?.error?.detail || 'Unable to switch family.';
        this.cdr.detectChanges();
      },
    });
  }

  loadSettings(): void {
    this.loading = true;
    this.error = '';

    this.settingsApi.getSettings().subscribe({
      next: (response) => {
        this.profile = response.profile;
        this.preferences = { ...response.preferences };
        this.email = response.profile.email;
        this.loading = false;
        this.cdr.detectChanges();
      },
      error: (error) => {
        console.error('Settings loading error:', error);
        this.loading = false;
        this.error = error?.error?.detail || 'Unable to load settings.';
        this.cdr.detectChanges();
      },
    });
  }

  loadTransactionHistory(): void {
    this.transactionHistoryLoading = true;
    this.transactionHistoryError = '';

    this.settingsApi.getTransactionEditHistory().subscribe({
      next: (response) => {
        this.transactionHistory = response.results || [];
        this.transactionHistoryLoading = false;
        this.historyPage = 1;
        this.expandedHistoryId = null;
        this.cdr.detectChanges();
      },
      error: (error) => {
        console.error('Transaction edit history loading error:', error);
        this.transactionHistoryLoading = false;
        this.transactionHistoryError =
          error?.error?.detail || 'Unable to load transaction edit history.';
        this.cdr.detectChanges();
      },
    });
  }

  get filteredTransactionHistory(): TransactionEditHistory[] {
    const search = this.historySearch.trim().toLowerCase();

    return this.transactionHistory.filter((history) => {
      const matchesSearch = !search || [
        history.transaction_id?.toString() || '',
        history.asset_name || '',
        history.edited_by_username || '',
        ...history.changed_fields,
      ].some((value) => value.toLowerCase().includes(search));

      const matchesEditor =
        !this.historyEditorFilter || history.edited_by_username === this.historyEditorFilter;

      const matchesDate =
        !this.historyDateFilter || history.edited_at.slice(0, 10) === this.historyDateFilter;

      return matchesSearch && matchesEditor && matchesDate;
    });
  }

  get historyEditors(): string[] {
    return Array.from(
      new Set(this.transactionHistory.map((history) => history.edited_by_username)),
    ).sort((a, b) => a.localeCompare(b));
  }

  get historyPageCount(): number {
    return Math.max(1, Math.ceil(this.filteredTransactionHistory.length / this.historyPageSize));
  }

  get pagedTransactionHistory(): TransactionEditHistory[] {
    const maxPage = this.historyPageCount;
    if (this.historyPage > maxPage) {
      this.historyPage = maxPage;
    }

    const start = (this.historyPage - 1) * this.historyPageSize;
    return this.filteredTransactionHistory.slice(start, start + this.historyPageSize);
  }

  get historyPageNumbers(): number[] {
    return Array.from({ length: this.historyPageCount }, (_, index) => index + 1);
  }

  applyHistoryFilters(): void {
    this.historyPage = 1;
    this.expandedHistoryId = null;
  }

  clearHistoryFilters(): void {
    this.historySearch = '';
    this.historyEditorFilter = '';
    this.historyDateFilter = '';
    this.historyPage = 1;
    this.expandedHistoryId = null;
  }

  changeHistoryPage(page: number): void {
    if (page < 1 || page > this.historyPageCount) return;
    this.historyPage = page;
    this.expandedHistoryId = null;
  }

  toggleHistory(historyId: number): void {
    this.expandedHistoryId =
      this.expandedHistoryId === historyId ? null : historyId;
  }

  formatHistoryField(field: string): string {
    const labels: Record<string, string> = {
      family_name: 'Family Name',
      portfolio: 'Portfolio',
      asset_class: 'Asset Class',
      sub_class: 'Sub Class',
      asset_name: 'Asset Name',
      underlying: 'Underlying',
      advisors: 'Advisor',
      transaction_date: 'Transaction Date',
      transaction_type: 'Transaction Type',
      quantity: 'Quantity',
      price_per_unit: 'Price / Unit',
      amount: 'Amount',
      fees: 'Fees',
      notes: 'Notes',
      source: 'Source',
      source_key: 'Source Key',
      asset_id: 'Asset ID',
      isin: 'ISIN',
    };

    return labels[field] || field;
  }

  formatHistoryValue(value: string | number | null | undefined): string {
    if (value === null || value === undefined || value === '') {
      return '—';
    }

    return String(value);
  }

  saveSettings(): void {
    if (this.saving) return;

    this.saving = true;
    this.profileMessage = '';
    this.error = '';

    this.settingsApi.updateSettings({
      email: this.email,
      currency: this.preferences.currency,
      date_format: this.preferences.date_format,
      default_analytics_period: this.preferences.default_analytics_period,
    }).subscribe({
      next: (response) => {
        this.profile = response.profile;
        this.preferences = { ...response.preferences };
        this.email = response.profile.email;
        this.saving = false;
        this.profileMessage = 'Settings saved successfully.';
        this.cdr.detectChanges();
      },
      error: (error) => {
        console.error('Settings save error:', error);
        this.saving = false;
        this.error = error?.error?.detail || 'Unable to save settings.';
        this.cdr.detectChanges();
      },
    });
  }

  changePassword(): void {
    if (this.changingPassword) return;

    this.passwordError = '';
    this.passwordMessage = '';

    if (!this.currentPassword) {
      this.passwordError = 'Enter your current password.';
      return;
    }

    if (!this.newPassword) {
      this.passwordError = 'Enter a new password.';
      return;
    }

    if (this.newPassword !== this.confirmPassword) {
      this.passwordError = 'New passwords do not match.';
      return;
    }

    this.changingPassword = true;

    this.settingsApi.changePassword(
      this.currentPassword,
      this.newPassword,
      this.confirmPassword,
    ).subscribe({
      next: () => {
        this.changingPassword = false;
        this.currentPassword = '';
        this.newPassword = '';
        this.confirmPassword = '';
        this.passwordMessage = 'Password changed successfully.';
        this.cdr.detectChanges();
      },
      error: (error) => {
        console.error('Password change error:', error);
        this.changingPassword = false;
        const detail = error?.error?.detail;
        this.passwordError = Array.isArray(detail)
          ? detail.join(' ')
          : detail || 'Unable to change password.';
        this.cdr.detectChanges();
      },
    });
  }

  logout(): void {
    if (this.loggingOut) return;

    const confirmed = window.confirm('Are you sure you want to log out?');
    if (!confirmed) return;

    this.loggingOut = true;
    this.error = '';

    this.authService.logout().subscribe({
      next: () => {
        this.loggingOut = false;
        this.router.navigate(['/login']);
      },
      error: (error) => {
        console.error('Settings logout error:', error);
        this.loggingOut = false;
        this.router.navigate(['/login']);
      },
    });
  }

  refresh(): void {
    this.loadSettings();
    this.rbac.load().subscribe();

    if (this.activeTab === 'transaction-history') {
      this.loadTransactionHistory();
    }
  }
}
