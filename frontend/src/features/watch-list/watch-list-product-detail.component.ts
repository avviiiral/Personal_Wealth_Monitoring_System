import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, RouterModule } from '@angular/router';

import { WatchListApiService, WatchListProduct } from '../../core/services/watch-list-api.service';

@Component({
  selector: 'app-watch-list-product-detail',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <section class="product-detail-page">
      <button type="button" class="back-link" routerLink="/watch-list">← Back to Watch List</button>

      <div *ngIf="loading" class="state">Loading product source data…</div>
      <div *ngIf="error" class="error">{{ error }}</div>

      <ng-container *ngIf="!loading && product as p">
        <header class="detail-header">
          <div>
            <span class="eyebrow">{{ p.product_type === 'PMS' ? 'PORTFOLIO MANAGEMENT SERVICE' : 'MUTUAL FUND' }}</span>
            <h1>{{ p.name }}</h1>
            <p>{{ p.provider || 'Provider unavailable' }}</p>
          </div>
          <a *ngIf="externalProductUrl(p) as sourceUrl" class="source-button" [href]="sourceUrl" target="_blank" rel="noopener noreferrer">Open External Source ↗</a>
        </header>

        <section class="detail-section">
          <h2>Product Source Data</h2>
          <div class="detail-grid">
            <div><span>Provider</span><strong>{{ p.provider || '—' }}</strong></div>
            <div><span>Fund Manager</span><strong>{{ p.mutual_fund?.fund_manager || p.pms?.fund_manager || '—' }}</strong></div>
            <div><span>ISIN</span><strong>{{ p.isin || 'Not applicable / unavailable' }}</strong></div>
            <div><span>Source Identifier</span><strong>{{ p.external_identifier || p.mutual_fund?.scheme_code || '—' }}</strong></div>
            <div><span>Category</span><strong>{{ p.category || '—' }}</strong></div>
            <div><span>Sub-category</span><strong>{{ p.sub_category || '—' }}</strong></div>
            <div><span>Benchmark</span><strong>{{ p.benchmark || '—' }}</strong></div>
            <div><span>Comparison Benchmark</span><strong>{{ p.comparison_benchmark || '—' }}</strong></div>
            <div><span>Source</span><strong>{{ p.source || '—' }}</strong></div>
            <div><span>Source Date</span><strong>{{ p.source_date || '—' }}</strong></div>
            <div class="wide"><span>Source Reference</span><strong class="break-value">{{ p.source_reference || '—' }}</strong></div>
          </div>
        </section>

        <section class="detail-section">
          <h2>{{ p.product_type === 'PMS' ? 'PMS Strategy Details' : 'Mutual Fund Details' }}</h2>
          <div class="detail-grid" *ngIf="p.product_type === 'PMS'">
            <div><span>Strategy Name</span><strong>{{ p.pms?.strategy_name || '—' }}</strong></div>
            <div><span>Strategy Type</span><strong>{{ p.pms?.strategy_type || '—' }}</strong></div>
            <div><span>Asset Class</span><strong>{{ p.pms?.asset_class || '—' }}</strong></div>
            <div><span>Minimum Investment</span><strong>{{ amount(p.pms?.minimum_investment) }}</strong></div>
            <div><span>Inception Date</span><strong>{{ p.pms?.inception_date || '—' }}</strong></div>
            <div><span>Risk Information</span><strong>{{ p.pms?.risk_information || '—' }}</strong></div>
            <div><span>AUM</span><strong>{{ amount(p.pms?.aum) }}</strong></div>
            <div><span>Latest Value</span><strong>{{ amount(p.pms?.latest_value) }}</strong></div>
          </div>
          <div class="detail-grid" *ngIf="p.product_type === 'MUTUAL_FUND'">
            <div><span>Scheme Code</span><strong>{{ p.mutual_fund?.scheme_code || '—' }}</strong></div>
            <div><span>Fund Type</span><strong>{{ p.mutual_fund?.fund_type || '—' }}</strong></div>
            <div><span>Plan</span><strong>{{ p.mutual_fund?.plan || '—' }}</strong></div>
            <div><span>Option</span><strong>{{ p.mutual_fund?.option || '—' }}</strong></div>
            <div><span>Expense Ratio</span><strong>{{ p.mutual_fund?.expense_ratio ?? '—' }}</strong></div>
            <div><span>Minimum Investment</span><strong>{{ amount(p.mutual_fund?.minimum_investment) }}</strong></div>
            <div><span>Inception Date</span><strong>{{ p.mutual_fund?.inception_date || '—' }}</strong></div>
            <div><span>Exit Load</span><strong>{{ p.mutual_fund?.exit_load || '—' }}</strong></div>
            <div><span>AUM</span><strong>{{ amount(p.mutual_fund?.aum) }}</strong></div>
            <div><span>Latest NAV</span><strong>{{ p.mutual_fund?.latest_nav ?? '—' }}</strong></div>
            <div><span>NAV Date</span><strong>{{ p.mutual_fund?.latest_nav_date || '—' }}</strong></div>
          </div>
        </section>

        <section class="detail-section">
          <h2>Performance History</h2>
          <div class="table-wrap" *ngIf="p.performance?.length; else noPerformance">
            <table>
              <thead><tr><th>Date</th><th>NAV / Value</th><th>AUM</th><th>1M</th><th>3M</th><th>6M</th><th>1Y</th><th>3Y</th><th>5Y</th><th>CAGR</th><th>Source</th></tr></thead>
              <tbody>
                <tr *ngFor="let item of p.performance">
                  <td>{{ item.date || '—' }}</td><td>{{ item.nav_or_value ?? '—' }}</td><td>{{ amount(item.aum) }}</td>
                  <td>{{ percent(item.return_1m) }}</td><td>{{ percent(item.return_3m) }}</td>
                  <td>{{ percent(item.return_6m) }}</td><td>{{ percent(item.return_1y) }}</td>
                  <td>{{ percent(item.return_3y) }}</td><td>{{ percent(item.return_5y) }}</td>
                  <td>{{ percent(item.cagr) }}</td><td>{{ item.source || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <ng-template #noPerformance><p class="muted">No performance snapshots are currently available for this product.</p></ng-template>
        </section>
      </ng-container>
    </section>
  `,
  styles: [`
    :host { display:block; color:var(--ink); }
    .product-detail-page { padding:20px; max-width:1500px; margin:0 auto; }
    .back-link { border:0; background:transparent; color:var(--accent); padding:0; margin-bottom:18px; cursor:pointer; font:inherit; }
    .detail-header { display:flex; justify-content:space-between; align-items:center; gap:18px; padding:20px; border:1px solid var(--surface-line); border-radius:12px; background:var(--surface); margin-bottom:16px; }
    .eyebrow { color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.08em; }
    h1 { margin:7px 0; font-size:24px; }
    .detail-header p { margin:0; color:var(--muted); }
    .source-button { padding:10px 14px; border:1px solid var(--surface-line); border-radius:8px; color:var(--ink); text-decoration:none; white-space:nowrap; }
    .detail-section { padding:18px; border:1px solid var(--surface-line); border-radius:12px; background:var(--surface); margin-bottom:16px; }
    h2 { margin:0 0 16px; font-size:16px; }
    .detail-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }
    .detail-grid > div { min-width:0; padding:12px; border:1px solid var(--surface-line); border-radius:8px; }
    .detail-grid span { display:block; color:var(--muted); font-size:11px; margin-bottom:6px; }
    .detail-grid strong { display:block; font-size:13px; overflow-wrap:anywhere; }
    .detail-grid .wide { grid-column:1/-1; }
    .break-value { font-weight:500; }
    .table-wrap { overflow-x:auto; }
    table { width:100%; border-collapse:collapse; white-space:nowrap; font-size:12px; }
    th,td { padding:11px 12px; border-bottom:1px solid var(--surface-line); text-align:center; }
    th { color:var(--muted); font-weight:700; }
    .muted,.state { color:var(--muted); }
    .error { padding:14px; color:var(--danger,#dc2626); }
    @media(max-width:900px) { .detail-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media(max-width:560px) { .product-detail-page { padding:10px; } .detail-header { align-items:flex-start; flex-direction:column; } .detail-grid { grid-template-columns:1fr; } .detail-grid .wide { grid-column:auto; } h1 { font-size:20px; } }
  `],
})
export class WatchListProductDetailComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly api = inject(WatchListApiService);

  product: WatchListProduct | null = null;
  loading = true;
  error = '';

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    if (!Number.isInteger(id) || id <= 0) {
      this.loading = false;
      this.error = 'Invalid product identifier.';
      return;
    }
    this.api.getProduct(id).subscribe({
      next: product => { this.product = product; this.loading = false; },
      error: () => { this.error = 'Unable to load this product source data. It may have been removed or is no longer available.'; this.loading = false; },
    });
  }

  externalProductUrl(product: WatchListProduct): string | null {
    const candidates = [product.source_reference, product.official_website];
    for (const candidate of candidates) {
      if (!candidate) continue;
      try {
        const url = new URL(candidate);
        if (url.protocol === 'http:' || url.protocol === 'https:') return url.href;
      } catch {
        // Source references may be identifiers rather than web URLs.
      }
    }
    return null;
  }

  amount(value: number | string | null | undefined): string {
    if (value === null || value === undefined || value === '') return '—';
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(Number(value));
  }

  percent(value: number | string | null | undefined): string {
    if (value === null || value === undefined || value === '') return '—';
    return `${Number(value).toFixed(2)}%`;
  }
}
