import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  MutualFundAnalyticsFund,
  MutualFundAnalyticsResponse,
  MutualFundAnalyticsService,
} from '../../core/services/mutual-fund-analytics.service';

type Period = '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y';

@Component({
  selector: 'app-mutual-fund-analytics',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mutual-fund-analytics.component.html',
  styleUrl: './mutual-fund-analytics.component.scss',
})
export class MutualFundAnalyticsComponent implements OnInit {
  private readonly analytics = inject(MutualFundAnalyticsService);

  data: MutualFundAnalyticsResponse | null = null;
  loading = false;
  error = '';
  category = '';
  provider = '';
  plan = '';
  benchmark = '';
  rankingPeriod: Period = '1Y';

  readonly periods: Period[] = ['1M', '3M', '6M', '1Y', '3Y', '5Y'];
  readonly benchmarkOptions = ['Nifty 50', 'Nifty 100', 'Nifty Small Cap 250', 'Nifty Mid Cap 150', 'BSE 500'];

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    this.error = '';
    this.analytics.getOverview({
      category: this.category,
      provider: this.provider,
      plan: this.plan,
      benchmark: this.benchmark,
      ranking_period: this.rankingPeriod,
    }).subscribe({
      next: response => {
        this.data = response;
        this.loading = false;
      },
      error: err => {
        console.error('Unable to load mutual fund analytics:', err);
        this.error = err?.error?.detail || 'Unable to load mutual fund analytics. Please try again.';
        this.loading = false;
      },
    });
  }

  get funds(): MutualFundAnalyticsFund[] {
    return this.data?.funds ?? [];
  }

  get rankedFunds(): MutualFundAnalyticsFund[] {
    return this.data?.rankings ?? [];
  }

  get averageReturn(): number | null {
    const value = this.data?.summary.average_returns?.[this.rankingPeriod]?.average_return;
    return value === null || value === undefined ? null : Number(value);
  }

  get benchmarkReturn(): number | null {
    const value = this.data?.benchmark?.periods?.[this.rankingPeriod];
    return value === null || value === undefined ? null : Number(value);
  }

  get excessReturn(): number | null {
    return this.averageReturn === null || this.benchmarkReturn === null
      ? null
      : this.averageReturn - this.benchmarkReturn;
  }

  formatPercent(value: number | string | null | undefined): string {
    if (value === null || value === undefined || value === '') return '—';
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${numeric >= 0 ? '+' : ''}${numeric.toFixed(2)}%` : '—';
  }

  formatCurrency(value: number | string | null | undefined): string {
    if (value === null || value === undefined || value === '') return '—';
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '—';
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(numeric);
  }

  returnClass(value: number | string | null | undefined): string {
    if (value === null || value === undefined || value === '') return '';
    return Number(value) >= 0 ? 'positive' : 'negative';
  }

  trackByFund(_index: number, fund: MutualFundAnalyticsFund): number {
    return fund.id;
  }
}
