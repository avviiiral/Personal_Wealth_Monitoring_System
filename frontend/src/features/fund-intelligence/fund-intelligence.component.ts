import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnDestroy, OnInit, ViewChild, computed, inject, signal } from '@angular/core';
import { Chart, ChartConfiguration, registerables } from 'chart.js';

import {
  FundIntelligenceApiService,
  FundIntelligenceFund,
  FundIntelligenceSnapshot,
  PeriodKey,
  ReturnKey,
} from '../../core/services/fund-intelligence-api.service';

Chart.register(...registerables);

type SortKey = 'name' | 'aum' | 'expense_ratio' | ReturnKey;

const PERIODS: Array<{ key: PeriodKey; label: string }> = [
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: '6m', label: '6M' },
  { key: '1y', label: '1Y' },
  { key: '3y', label: '3Y' },
  { key: '5y', label: '5Y' },
];

/** Display rounding is 2 decimals; stored and returned values keep 4. */
function fmt(value: number | null | undefined, suffix = ''): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return `${value.toFixed(2)}${suffix}`;
}

@Component({
  selector: 'app-fund-intelligence',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './fund-intelligence.component.html',
  styleUrl: './fund-intelligence.component.scss',
})
export class FundIntelligenceComponent implements OnInit, OnDestroy {
  @ViewChild('returnsChart') returnsChartRef?: ElementRef<HTMLCanvasElement>;

  private readonly api = inject(FundIntelligenceApiService);
  private chart?: Chart;

  readonly periods = PERIODS;
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly snapshot = signal<FundIntelligenceSnapshot | null>(null);
  readonly selectedId = signal<number | null>(null);
  readonly sortKey = signal<SortKey>('1y');
  readonly sortDir = signal<1 | -1>(-1);

  readonly funds = computed<FundIntelligenceFund[]>(() => this.snapshot()?.funds ?? []);

  readonly sortedFunds = computed(() => {
    const key = this.sortKey();
    const dir = this.sortDir();
    return [...this.funds()].sort((a, b) => {
      const left = this.sortValue(a, key);
      const right = this.sortValue(b, key);
      if (left === null && right === null) return 0;
      if (left === null) return 1;
      if (right === null) return -1;
      return (left === right ? 0 : left > right ? 1 : -1) * dir;
    });
  });

  readonly selectedFund = computed<FundIntelligenceFund | null>(() => {
    const funds = this.funds();
    return funds.find((fund) => fund.product_id === this.selectedId()) ?? funds[0] ?? null;
  });

  readonly kpis = computed(() => {
    const snapshot = this.snapshot();
    if (!snapshot || snapshot.funds.length === 0) return null;
    const funds = snapshot.funds;
    const ranked = funds.filter((fund) => fund.returns['1y'] !== null);
    const best = ranked.reduce<FundIntelligenceFund | null>(
      (top, fund) => (!top || (fund.returns['1y'] ?? -Infinity) > (top.returns['1y'] ?? -Infinity) ? fund : top),
      null,
    );
    const erFunds = funds.filter((fund) => fund.expense_ratio !== null);
    const lowestEr = erFunds.reduce<FundIntelligenceFund | null>(
      (low, fund) => (!low || (fund.expense_ratio ?? Infinity) < (low.expense_ratio ?? Infinity) ? fund : low),
      null,
    );
    const aumFunds = funds.filter((fund) => fund.aum !== null);
    return {
      count: funds.length,
      categoryAverage1y: snapshot.category_average['1y'],
      best,
      lowestEr,
      totalAum: aumFunds.length ? aumFunds.reduce((sum, fund) => sum + (fund.aum ?? 0), 0) : null,
    };
  });

  ngOnInit(): void {
    this.load();
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
  }

  load(includeLive = true): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.getSnapshot(includeLive).subscribe({
      next: (snapshot) => {
        this.snapshot.set(snapshot);
        this.loading.set(false);
        if (this.selectedId() === null && snapshot.funds.length) {
          this.selectedId.set(snapshot.funds[0].product_id);
        }
        setTimeout(() => this.renderChart());
      },
      error: () => {
        this.error.set('Could not load Fund Intelligence data. Check that you are signed in and the backend is running.');
        this.loading.set(false);
      },
    });
  }

  selectFund(productId: number | string): void {
    this.selectedId.set(Number(productId));
    setTimeout(() => this.renderChart());
  }

  sortBy(key: SortKey): void {
    if (this.sortKey() === key) {
      this.sortDir.set(this.sortDir() === 1 ? -1 : 1);
    } else {
      this.sortKey.set(key);
      this.sortDir.set(key === 'name' ? 1 : -1);
    }
  }

  format(value: number | null | undefined, suffix = ''): string {
    return fmt(value, suffix);
  }

  formatAum(value: number | null | undefined): string {
    if (value === null || value === undefined) return '—';
    return value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  relativeClass(value: number | null | undefined): string {
    if (value === null || value === undefined) return 'neutral';
    return value >= 0 ? 'up' : 'down';
  }

  private sortValue(fund: FundIntelligenceFund, key: SortKey): number | string | null {
    if (key === 'name') return fund.short_name;
    if (key === 'aum') return fund.aum;
    if (key === 'expense_ratio') return fund.expense_ratio;
    return fund.returns[key];
  }

  private renderChart(): void {
    const canvas = this.returnsChartRef?.nativeElement;
    const fund = this.selectedFund();
    const snapshot = this.snapshot();
    if (!canvas || !fund || !snapshot) return;

    const config: ChartConfiguration<'bar'> = {
      type: 'bar',
      data: {
        labels: PERIODS.map((period) => period.label),
        datasets: [
          {
            label: fund.short_name,
            data: PERIODS.map((period) => fund.returns[period.key]),
            backgroundColor: '#FD7740',
          },
          {
            label: 'Category average',
            data: PERIODS.map((period) => snapshot.category_average[period.key]),
            backgroundColor: '#051233',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
        scales: { y: { title: { display: true, text: 'Return (%)' } } },
      },
    };

    this.chart?.destroy();
    this.chart = new Chart(canvas, config);
  }
}
