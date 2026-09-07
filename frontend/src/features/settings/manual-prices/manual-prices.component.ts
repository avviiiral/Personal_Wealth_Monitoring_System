import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ManualPriceService } from '../../../core/services/manual-price.service';
import { ToastService } from '../../../core/services/toast.service';

import {
  SettingsPriceApiService,
  SettingsPriceRow,
} from '../../../core/services/settings-price-api.service';

@Component({
  selector: 'app-manual-prices',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './manual-prices.component.html',
  styleUrl: './manual-prices.component.scss',
})
export class ManualPricesComponent implements OnInit {
  private readonly api = inject(SettingsPriceApiService);

  private readonly manualPriceService = inject(ManualPriceService);

  private readonly toast = inject(ToastService);

  private readonly cdr = inject(ChangeDetectorRef);

  rows: SettingsPriceRow[] = [];

  loading = true;

  error = '';

  editingAssetId: number | null = null;

  priceInput = '';

  saving = false;

  ngOnInit(): void {
    this.loadPrices();
  }

  loadPrices(): void {
    this.loading = true;

    this.error = '';

    this.api.listPrices().subscribe({
      next: (response) => {
        this.rows = response.results;

        this.loading = false;

        this.cdr.detectChanges();
      },

      error: (err) => {
        this.loading = false;

        this.error = err?.error?.detail || 'Unable to load prices.';

        this.cdr.detectChanges();
      },
    });
  }

  startEdit(row: SettingsPriceRow): void {
    this.editingAssetId = row.asset_id;
    this.priceInput = row.price ?? '';
  }

  cancelEdit(): void {
    this.editingAssetId = null;
    this.priceInput = '';
  }

  saveEdit(row: SettingsPriceRow): void {
    const price = Number(this.priceInput);

    if (!price || price <= 0 || Number.isNaN(price)) {
      this.toast.error('Enter a valid price greater than 0.');
      return;
    }

    this.saving = true;

    this.manualPriceService.updatePrice(row.asset_id, price).subscribe({
      next: (response) => {
        this.saving = false;

        if (!response.success) {
          this.toast.error(response.message || 'Unable to update price.');
          return;
        }

        this.toast.success(`Price for "${row.asset_name}" updated successfully.`);

        this.cancelEdit();
        this.loadPrices();
      },

      error: (err) => {
        this.saving = false;

        this.toast.error(err?.error?.message || err?.error?.detail || 'Unable to update price.');

        this.cdr.detectChanges();
      },
    });
  }

  restoreAutomatic(row: SettingsPriceRow): void {
    const confirmed = window.confirm(
      `Remove the manual override for "${row.asset_name}" and restore automatic pricing?`,
    );

    if (!confirmed) {
      return;
    }

    this.manualPriceService.deletePrice(row.asset_id).subscribe({
      next: (response) => {
        if (!response.success) {
          this.toast.error(response.message || 'Unable to restore automatic pricing.');
          return;
        }

        this.toast.success(`Automatic pricing restored for "${row.asset_name}".`);

        this.loadPrices();
      },

      error: (err) => {
        this.toast.error(
          err?.error?.message || err?.error?.detail || 'Unable to restore automatic pricing.',
        );
      },
    });
  }

  formatDate(value: string | null): string {
    if (!value) {
      return '-';
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return value;
    }

    return date.toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}
