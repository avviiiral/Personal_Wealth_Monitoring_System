import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { forkJoin } from 'rxjs';
import {
  IndiaMutualFundFeedResponse,
  MutualFundResearchReportResponse,
  MutualFundAnalyticsService,
} from '../../core/services/mutual-fund-analytics.service';

type Period = '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y' | 'YTD';

@Component({
  selector: 'app-mutual-fund-analytics',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mutual-fund-analytics.component.html',
  styleUrl: './mutual-fund-analytics.component.scss',
})
export class MutualFundAnalyticsComponent implements OnInit {
  private readonly analytics = inject(MutualFundAnalyticsService);

  report: MutualFundResearchReportResponse | null = null;
  indiaFeed: IndiaMutualFundFeedResponse | null = null;
  loading = false;
  error = '';
  activeTab = 'overview';
  selectedFundName = '';
  activePeriod: Period = '1Y';
  xAxis = 'large';
  yAxis = 'edge_score';
  bubbleAxis = 'aum';

  readonly tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'scorecard', label: 'Fund Scorecard' },
    { id: 'edge', label: 'EDGE Scores' },
    { id: 'calendar', label: 'Calendar Returns' },
    { id: 'performance', label: 'Performance' },
    { id: 'portfolio', label: 'Portfolio' },
    { id: 'positioning', label: 'Positioning' },
    { id: 'universe', label: 'Universe' },
    { id: 'attribution', label: 'Attribution' },
    { id: 'india', label: 'India Universe' },
  ];
  readonly periods: Period[] = ['1M', '3M', '6M', '1Y', '3Y', '5Y', 'YTD'];
  readonly axisOptions = [
    { key: 'large', label: 'Large Cap %' }, { key: 'mid', label: 'Mid Cap %' },
    { key: 'small', label: 'Small Cap %' }, { key: 'cash', label: 'Cash %' },
    { key: 'cyclical', label: 'Cyclical %' }, { key: 'defensive', label: 'Defensive %' },
    { key: 'sensitive', label: 'Sensitive %' }, { key: 'pe', label: 'P/E' },
    { key: 'pbv', label: 'P/B' }, { key: 'beta', label: 'Beta' },
    { key: 'sharpe_calc', label: 'Sharpe (3Y)' }, { key: 'sortino', label: 'Sortino' },
    { key: 'max_drawdown', label: 'Max Drawdown %' }, { key: 'calmar', label: 'Calmar' },
    { key: 'r3m', label: '3M Return %' }, { key: 'r6m', label: '6M Return %' },
    { key: 'r1y', label: '1Y Return %' }, { key: 'edge_score', label: 'EDGE Score' },
    { key: 'eps_cagr', label: 'EPS CAGR %' }, { key: 'fwd_peg', label: 'Forward PEG' },
    { key: 'quality', label: 'Quality' }, { key: 'valuation', label: 'Valuation' },
    { key: 'momentum', label: 'Momentum' }, { key: 'top10', label: 'Top-10 %' },
    { key: 'stocks', label: 'Stock Count' }, { key: 'aum', label: 'AUM' },
    { key: 'er', label: 'Expense Ratio %' }, { key: 'turnover', label: 'Turnover %' },
  ];

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading = true;
    this.error = '';
    forkJoin({
      report: this.analytics.getResearchReport(),
      indiaFeed: this.analytics.getIndiaMarketFeed(),
    }).subscribe({
      next: value => {
        this.report = value.report;
        this.indiaFeed = value.indiaFeed;
        if (value.report.available && !this.selectedFundName) {
          this.selectedFundName = value.report.payload?.RAW?.funds?.[0]?.name || '';
        }
        this.loading = false;
      },
      error: err => {
        this.error = err?.error?.detail || 'Unable to load mutual-fund research data.';
        this.loading = false;
      },
    });
  }

  get raw(): any { return this.report?.payload?.RAW || null; }
  get p2p(): any { return this.report?.payload?.P2P || {}; }
  get attr(): any { return this.report?.payload?.ATTR || {}; }
  get funds(): any[] { return this.raw?.funds || []; }
  get category(): any { return this.raw?.cat || {}; }
  get selectedFund(): any {
    return this.funds.find(f => f.name === this.selectedFundName) || this.funds[0] || null;
  }
  get selectedAttribution(): any {
    return this.selectedFund ? this.attr?.per_fund?.[this.selectedFund.name] || null : null;
  }
  get edgeRanked(): any[] {
    return [...this.funds].sort((a, b) => this.numeric(b.edge_score) - this.numeric(a.edge_score));
  }
  get rankedFunds(): any[] {
    return [...this.funds].filter(f => this.fundReturn(f, this.activePeriod) !== null)
      .sort((a, b) => this.fundReturn(b, this.activePeriod)! - this.fundReturn(a, this.activePeriod)!);
  }
  get positioningFunds(): any[] {
    return this.funds.filter(f => this.axisValue(f, this.xAxis) !== null && this.axisValue(f, this.yAxis) !== null)
      .sort((a, b) => this.numeric(b[this.yAxis]) - this.numeric(a[this.yAxis]));
  }
  get sectorRows(): Array<{ name: string; value: number }> {
    return Object.entries(this.selectedFund?.sectors || {})
      .map(([name, value]) => ({ name, value: this.numeric(value) }))
      .sort((a, b) => b.value - a.value);
  }
  get selectedFundTrend(): any[] {
    const fund = this.selectedFund;
    if (!fund) return [];
    return (fund.ts_large || []).map((large: any, index: number) => ({
      month: this.raw?.months?.[index] || String(index + 1),
      large,
      mid: fund.ts_mid?.[index] ?? null,
      small: fund.ts_small?.[index] ?? null,
      cash: fund.ts_cash?.[index] ?? null,
      pe: fund.pe_ts?.[index] ?? null,
      aum: fund.aum_ts?.[index] ?? null,
    }));
  }
  get topSectorRows(): Array<{ sector: string; total_contrib: number; fund_count: number; stocks: number; avg_pr: number }> {
    return [...(this.attr?.category_sector || [])].sort((a, b) => this.numeric(b.total_contrib) - this.numeric(a.total_contrib)).slice(0, 12);
  }
  get selectedReturnRanks(): Record<string, number | null> {
    const result: Record<string, number | null> = {};
    for (const p of this.periods) {
      const values = this.funds.map(f => this.fundReturn(f, p)).filter((v): v is number => v !== null).sort((a, b) => b - a);
      const value = this.selectedFund ? this.fundReturn(this.selectedFund, p) : null;
      result[p] = value === null ? null : values.findIndex(v => v === value) + 1;
    }
    return result;
  }

  fundReturn(fund: any, period: string): number | null {
    const key = ({ '1M': 'r1m', '3M': 'r3m', '6M': 'r6m', '1Y': 'r1y', '3Y': 'r3y', '5Y': 'r5y', 'YTD': 'ytd' } as Record<string, string>)[period];
    const p2pFund = this.p2p?.funds?.[fund?.name];
    if (!p2pFund) return null;
    const value = p2pFund[key];
    return value === null || value === undefined || value === '' ? null : this.numeric(value);
  }

  axisValue(fund: any, key: string): number | null {
    const value = fund?.[key];
    return value === null || value === undefined || value === '' ? null : this.numeric(value);
  }

  quartile(fund: any, period: string): string {
    const value = this.fundReturn(fund, period);
    if (value === null) return '—';
    const values = this.funds.map(f => this.fundReturn(f, period)).filter((v): v is number => v !== null).sort((a, b) => b - a);
    const rank = values.findIndex(v => v === value) + 1;
    if (!rank || !values.length) return '—';
    return rank <= Math.ceil(values.length / 4) ? 'Q1' :
      rank <= Math.ceil(values.length / 2) ? 'Q2' :
      rank <= Math.ceil(values.length * 3 / 4) ? 'Q3' : 'Q4';
  }

  numeric(value: any): number {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }
  format(value: any, digits = 2): string {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits) : '—';
  }
  formatPct(value: any): string {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    return Number.isFinite(n) ? `${n > 0 ? '+' : ''}${n.toFixed(2)}%` : '—';
  }
  formatAum(value: any): string {
    if (value === null || value === undefined || value === '') return '—';
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Number(value));
  }
  axisLabel(key: string): string {
    return this.axisOptions.find(o => o.key === key)?.label || key;
  }
  benchmarkValue(period: string): any {
    return this.p2p?.bm?.vals?.[period.toLowerCase()] ?? null;
  }
  categoryPeriodReturn(period: string): any {
    const key = ({ '1M': 'r1m', '3M': 'r3m', '6M': 'r6m', '1Y': 'r1y', '3Y': 'r3y', '5Y': 'r5y', 'YTD': 'ytd' } as Record<string, string>)[period];
    return this.p2p?.cat?.[key] ?? null;
  }
  benchmarkCytd(name: string): any {
    return this.raw?.benchmarks?.cytd_bench?.[name] ?? null;
  }
  barWidth(value: any, max: any): number {
    const v = Math.abs(this.numeric(value));
    const m = Math.max(Math.abs(this.numeric(max)), 0.0001);
    return Math.max(2, Math.min(100, v / m * 100));
  }
  trackByName(_index: number, item: any): string { return item?.name || item?.fund_name || item?.company || item?.sector || String(_index); }
}
