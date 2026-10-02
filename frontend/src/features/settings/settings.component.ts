import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';

import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../core/services/auth.service';
import { RbacService } from '../../core/services/rbac.service';

import {
  SettingsApiService,
  SettingsProfile,
  TaxRateSetting,
  TaxRateChangeLog,
  TransactionEditHistory,
  TransactionUploadHistory,
  TransactionUploadDetail,
  UnderlyingUploadHistory,
  MISNotesHistoryEntry,
} from '../../core/services/settings-api.service';

import { UserManagementComponent } from './user-management/user-management.component';
import { FamilyManagementComponent } from './family-management/family-management.component';
import { ManualPricesComponent } from './manual-prices/manual-prices.component';

type SettingsTab =
  | 'account'
  | 'security'
  | 'users'
  | 'families'
  | 'prices'
  | 'tax-rates'
  | 'tax-updates'
  | 'transaction-history'
  | 'upload-history'
  | 'underlyings'
  | 'mis-history';

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

  taxRateSettings: TaxRateSetting[] = [];
  taxRateLoading = false;
  taxRateSavingAssetId: number | null = null;
  taxRateError = '';

  taxUpdateHistory: TaxRateChangeLog[] = [];
  taxUpdateLoading = false;
  taxUpdateError = '';

  transactionHistory: TransactionEditHistory[] = [];
  transactionHistoryLoading = false;
  transactionHistoryError = '';
  expandedHistoryId: number | null = null;

  transactionUploads: TransactionUploadHistory[] = [];
  transactionUploadLoading = false;
  transactionUploadError = '';
  expandedUploadId: number | null = null;
  selectedUpload: TransactionUploadDetail | null = null;
  uploadDetailLoading = false;

  underlyingUploads: UnderlyingUploadHistory[] = [];
  underlyingUploadLoading = false;
  underlyingUploadError = '';

  misNotesHistory: MISNotesHistoryEntry[] = [];
  misNotesHistoryLoading = false;
  misNotesHistoryError = '';

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

    if (tab === 'tax-rates' && !this.taxRateSettings.length) {
      this.loadTaxRateSettings();
    }

    if (tab === 'tax-updates' && !this.taxUpdateHistory.length) {
      this.loadTaxUpdateHistory();
    }

    if (tab === 'transaction-history' && !this.transactionHistory.length) {
      this.loadTransactionHistory();
    }

    if (tab === 'upload-history' && !this.transactionUploads.length) {
      this.loadTransactionUploadHistory();
    }

    if (tab === 'underlyings' && !this.underlyingUploads.length) {
      this.loadUnderlyingUploads();
    }

    if (tab === 'mis-history' && !this.misNotesHistory.length) {
      this.loadMISNotesHistory();
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
        if (this.activeTab === 'tax-rates') {
          this.loadTaxRateSettings();
        }
        if (this.activeTab === 'tax-updates') {
          this.loadTaxUpdateHistory();
        }
        if (this.activeTab === 'upload-history') {
          this.loadTransactionUploadHistory();
        }
        if (this.activeTab === 'underlyings') {
          this.loadUnderlyingUploads();
        }
        if (this.activeTab === 'mis-history') {
          this.loadMISNotesHistory();
        }
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


  loadTaxRateSettings(): void {
    this.taxRateLoading = true;
    this.taxRateError = '';

    this.settingsApi.getTaxRateSettings().subscribe({
      next: (rows) => {
        this.taxRateSettings = rows || [];
        this.taxRateLoading = false;
        this.cdr.detectChanges();
      },
      error: (error) => {
        this.taxRateLoading = false;
        this.taxRateError =
          error?.error?.detail || 'Unable to load tax settings for the active family.';
        this.cdr.detectChanges();
      },
    });
  }

  saveTaxRate(row: TaxRateSetting): void {
    if (this.taxRateSavingAssetId !== null) return;

    const tenureMonths = Number(row.tenure_months);
    const shortTermTaxRate = Number(row.short_term_tax_rate);
    const longTermTaxRate = Number(row.long_term_tax_rate);

    if (!Number.isInteger(tenureMonths) || tenureMonths < 0) {
      this.taxRateError = `Enter a valid non-negative tenure in months for "${row.asset_name}".`;
      return;
    }

    if (!Number.isFinite(shortTermTaxRate) || shortTermTaxRate < 0 || shortTermTaxRate > 100) {
      this.taxRateError = `Short Term Tax must be between 0 and 100 percent for "${row.asset_name}".`;
      return;
    }

    if (!Number.isFinite(longTermTaxRate) || longTermTaxRate < 0 || longTermTaxRate > 100) {
      this.taxRateError = `Long Term Tax must be between 0 and 100 percent for "${row.asset_name}".`;
      return;
    }

    this.taxRateSavingAssetId = row.asset_id;
    this.taxRateError = '';

    const request$ = row.id === null
      ? this.settingsApi.saveTaxRateSetting(
          row.asset_id,
          tenureMonths,
          shortTermTaxRate,
          longTermTaxRate,
        )
      : this.settingsApi.updateTaxRateSetting(
          row.id,
          tenureMonths,
          shortTermTaxRate,
          longTermTaxRate,
        );

    request$.subscribe({
      next: (saved) => {
        const index = this.taxRateSettings.findIndex((item) => item.asset_id === saved.asset_id);
        if (index >= 0) {
          this.taxRateSettings[index] = saved;
        }
        this.taxRateSavingAssetId = null;
        this.taxRateError = '';
        this.cdr.detectChanges();
      },
      error: (error) => {
        this.taxRateSavingAssetId = null;
        this.taxRateError =
          error?.error?.detail || `Unable to save tax settings for "${row.asset_name}".`;
        this.cdr.detectChanges();
      },
    });
  }

  clearTaxRate(row: TaxRateSetting): void {
    if (row.id === null || this.taxRateSavingAssetId !== null) return;

    if (!window.confirm(`Clear tax settings for "${row.asset_name}"?`)) return;

    this.taxRateSavingAssetId = row.asset_id;
    this.taxRateError = '';

    this.settingsApi.deleteTaxRateSetting(row.id).subscribe({
      next: () => {
        const index = this.taxRateSettings.findIndex((item) => item.asset_id === row.asset_id);
        if (index >= 0) {
          this.taxRateSettings[index] = {
            ...this.taxRateSettings[index],
            id: null,
            tenure_months: null,
            short_term_tax_rate: null,
            long_term_tax_rate: null,
            updated_at: null,
          };
        }
        this.taxRateSavingAssetId = null;
        this.cdr.detectChanges();
      },
      error: (error) => {
        this.taxRateSavingAssetId = null;
        this.taxRateError =
          error?.error?.detail || `Unable to clear tax settings for "${row.asset_name}".`;
        this.cdr.detectChanges();
      },
    });
  }

  loadTaxUpdateHistory(): void {
    this.taxUpdateLoading = true;
    this.taxUpdateError = '';

    this.settingsApi.getTaxRateChangeHistory().subscribe({
      next: (rows) => {
        this.taxUpdateHistory = rows || [];
        this.taxUpdateLoading = false;
        this.cdr.detectChanges();
      },
      error: (error) => {
        this.taxUpdateLoading = false;
        this.taxUpdateError =
          error?.error?.detail || 'Unable to load tax update history.';
        this.cdr.detectChanges();
      },
    });
  }

  formatTaxChange(change: TaxRateChangeLog['change_from']): string {
    const tenure = change.tenure_months === null ? '—' : `${change.tenure_months} months`;
    const shortTerm = change.short_term_tax_rate === null ? '—' : `${change.short_term_tax_rate}%`;
    const longTerm = change.long_term_tax_rate === null ? '—' : `${change.long_term_tax_rate}%`;

    return `Tenure: ${tenure} · Short Term: ${shortTerm} · Long Term: ${longTerm}`;
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


  loadMISNotesHistory(): void {
    this.misNotesHistoryLoading = true;
    this.misNotesHistoryError = '';

    this.settingsApi.getMISNotesHistory().subscribe({
      next: (response) => {
        this.misNotesHistory = response.results || [];
        this.misNotesHistoryLoading = false;
        this.cdr.detectChanges();
      },
      error: (error) => {
        this.misNotesHistoryLoading = false;
        this.misNotesHistoryError =
          error?.error?.detail || 'Unable to load MIS Notes edit history.';
        this.cdr.detectChanges();
      },
    });
  }

  formatMISNotesChanges(entry: MISNotesHistoryEntry): string {
    return entry.changes.map((change) => {
      const type = String(change['type'] || 'edited').replace(/_/g, ' ');
      const section = change['section'] ? `[Section: ${change['section']}] ` : '';
      const row = change['row'] ? `[Row: ${change['row']}] ` : '';
      const column = change['column'] ? `[Column: ${change['column']}] ` : '';
      const oldValue = change['old'];
      const newValue = change['new'];
      if (oldValue !== undefined || newValue !== undefined) {
        return `${type}: ${section}${row}${column}${oldValue ?? '—'} → ${newValue ?? '—'}`;
      }
      const label = newValue || oldValue || change['section'] || change['section_id'] || '';
      return `${type}: ${section}${label}`;
    }).join('; ');
  }


  loadTransactionUploadHistory(): void {
    this.transactionUploadLoading = true;
    this.transactionUploadError = '';
    this.settingsApi.getTransactionUploadHistory().subscribe({
      next: (response) => {
        this.transactionUploads = response.results || [];
        this.transactionUploadLoading = false;
        this.expandedUploadId = null;
        this.selectedUpload = null;
        this.cdr.detectChanges();
      },
      error: (error) => {
        this.transactionUploadLoading = false;
        this.transactionUploadError = error?.error?.detail || 'Unable to load transaction upload history.';
        this.cdr.detectChanges();
      },
    });
  }

  loadUnderlyingUploads(): void {
    this.underlyingUploadLoading = true;
    this.underlyingUploadError = '';

    this.settingsApi.getUnderlyingUploadHistory().subscribe({
      next: (response) => {
        this.underlyingUploads = response.results || [];
        this.underlyingUploadLoading = false;
        this.cdr.detectChanges();
      },
      error: (error) => {
        this.underlyingUploadLoading = false;
        this.underlyingUploadError =
          error?.error?.detail || 'Unable to load underlying upload history.';
        this.cdr.detectChanges();
      },
    });
  }

  toggleUpload(uploadId: number): void {
    if (this.expandedUploadId === uploadId) {
      this.expandedUploadId = null;
      this.selectedUpload = null;
      return;
    }
    this.expandedUploadId = uploadId;
    this.selectedUpload = null;
    this.uploadDetailLoading = true;
    this.settingsApi.getTransactionUploadDetail(uploadId).subscribe({
      next: (detail) => {
        this.selectedUpload = detail;
        this.uploadDetailLoading = false;
        this.cdr.detectChanges();
      },
      error: () => {
        this.uploadDetailLoading = false;
        this.transactionUploadError = 'Unable to load failed transaction details.';
        this.cdr.detectChanges();
      },
    });
  }

  downloadUnderlyingTemplate(): void {
    this.settingsApi.downloadUnderlyingTemplate().subscribe({
      next: (blob) => {
        const url = window.URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'sample_underlying_format.xlsx';
        anchor.click();
        window.URL.revokeObjectURL(url);
      },
      error: () => {
        this.underlyingUploadError = 'Unable to download the sample underlying Excel format.';
        this.cdr.detectChanges();
      },
    });
  }

  downloadTransactionTemplate(): void {
    this.settingsApi.downloadTransactionTemplate().subscribe({
      next: (blob) => {
        const url = window.URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'standard_transactions_format.xlsx';
        anchor.click();
        window.URL.revokeObjectURL(url);
      },
      error: () => {
        this.transactionUploadError = 'Unable to download the standard transaction format.';
        this.cdr.detectChanges();
      },
    });
  }

  getUploadStatusLabel(status: string): string {
    return status === 'PARTIAL' ? 'Partial' : status.charAt(0) + status.slice(1).toLowerCase();
  }

  formatChangedFields(history: TransactionEditHistory): string {
    return history.changed_fields.map((field) => this.formatHistoryField(field)).join(', ');
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
    }).subscribe({
      next: (response) => {
        this.profile = response.profile;
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

    if (this.activeTab === 'tax-rates') {
      this.loadTaxRateSettings();
    }

    if (this.activeTab === 'tax-updates') {
      this.loadTaxUpdateHistory();
    }

    if (this.activeTab === 'transaction-history') {
      this.loadTransactionHistory();
    }

    if (this.activeTab === 'upload-history') {
      this.loadTransactionUploadHistory();
    }

    if (this.activeTab === 'underlyings') {
      this.loadUnderlyingUploads();
    }

    if (this.activeTab === 'mis-history') {
      this.loadMISNotesHistory();
    }
  }
}
