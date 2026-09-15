import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { WatchListApiService, WatchListProduct } from '../../core/services/watch-list-api.service';

type ProductTab = 'MUTUAL_FUND' | 'PMS';
type StatusTab = 'ALL' | 'OWNED' | 'UNIVERSAL';

@Component({
  selector: 'app-watch-list',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './watch-list.component.html',
  styleUrl: './watch-list.component.scss',
})
export class WatchListComponent implements OnInit {
  private readonly api = inject(WatchListApiService);
  products: WatchListProduct[] = [];
  loading = true;
  error = '';
  search = '';
  provider = '';
  category = '';
  status: StatusTab = 'ALL';
  productTab: ProductTab = 'MUTUAL_FUND';
  ordering = 'name';
  count = 0;
  refreshing = false;

  readonly orderings = [
    { value: 'name', label: 'Name' }, { value: '1M', label: '1M Return' },
    { value: '3M', label: '3M Return' }, { value: '6M', label: '6M Return' },
    { value: '1Y', label: '1Y Return' }, { value: '3Y', label: '3Y Return' },
    { value: '5Y', label: '5Y Return' }, { value: 'cagr', label: 'CAGR' },
    { value: 'aum', label: 'AUM' },
  ];

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading = true;
    this.error = '';
    this.api.getProducts({
      product_type: this.productTab,
      status: this.status === 'ALL' ? undefined : this.status,
      search: this.search.trim() || undefined,
      provider: this.provider.trim() || undefined,
      category: this.category.trim() || undefined,
      ordering: this.ordering,
      page_size: 50,
    }).subscribe({
      next: response => { this.products = response.results; this.count = response.count; this.loading = false; },
      error: error => { console.error('Failed to load Watch List:', error); this.error = 'Unable to load Watch List right now.'; this.loading = false; },
    });
  }

  setProductTab(tab: ProductTab): void { if (this.productTab !== tab) { this.productTab = tab; this.load(); } }
  setStatus(status: StatusTab): void { if (this.status !== status) { this.status = status; this.load(); } }

  refreshUniverse(): void {
    if (this.refreshing) return;
    this.refreshing = true;
    this.api.refresh().subscribe({
      next: () => { this.refreshing = false; this.load(); },
      error: error => { console.error('Watch List refresh failed:', error); this.refreshing = false; this.error = 'Universe refresh failed. Existing data was not changed.'; },
    });
  }

  metric(product: WatchListProduct, key: string): number | null { return product.metrics?.[key] ?? null; }
  formatPercent(value: number | null): string { return value === null || value === undefined ? '—' : `${Number(value).toFixed(2)}%`; }
  formatAmount(value: number | null): string {
    if (value === null || value === undefined) return '—';
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(Number(value));
  }
}
