import {
  AfterViewInit,
  ChangeDetectorRef,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';

import { CommonModule } from '@angular/common';

import { Chart, ChartConfiguration, registerables } from 'chart.js';

import { WealthApiService, AnalyticsInsights, AnalyticsViewModel } from '../../core/services/wealth-api.service';

Chart.register(...registerables);

// Shared categorical palette for the Analytics page — used for the
// Allocation / Advisor pie & doughnut charts, their matching row
// swatches, and the advisor initials chips. Keeping one palette used
// everywhere means a color always means the same category or advisor
// across every chart and list on the page.
const CATEGORY_PALETTE_LIGHT = [
  '#111827', '#9c6b1f', '#0f6f66', '#3b5478',
  '#6d4a6b', '#8a5a3b', '#4b5563', '#7a3742',
];
const CATEGORY_PALETTE_DARK = [
  '#2fbf8f', '#e0a458', '#5fa8d3', '#94a3b8',
  '#c084b8', '#d69b70', '#aeb7c6', '#e77b86',
];
const GAIN_COLOR = '#157347';
const LOSS_COLOR = '#b42318';

@Component({
  selector: 'app-analytics',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './analytics.component.html',
  styleUrl: './analytics.component.scss',
})
export class AnalyticsComponent implements OnInit, AfterViewInit, OnDestroy {
  private readonly wealthApi = inject(WealthApiService);
  private readonly cdr = inject(ChangeDetectorRef);

  @ViewChild('historicalChart') historicalChartRef?: ElementRef<HTMLCanvasElement>;
  @ViewChild('allocationChart') allocationChartRef?: ElementRef<HTMLCanvasElement>;
  @ViewChild('performanceChart') performanceChartRef?: ElementRef<HTMLCanvasElement>;
  @ViewChild('advisorChart') advisorChartRef?: ElementRef<HTMLCanvasElement>;
  @ViewChild('advisorPerformanceChart') advisorPerformanceChartRef?: ElementRef<HTMLCanvasElement>;
  @ViewChild('marketCapChart') marketCapChartRef?: ElementRef<HTMLCanvasElement>;
  @ViewChild('sectorChart') sectorChartRef?: ElementRef<HTMLCanvasElement>;

  loading = true;
  error = '';
  summary: any = null;
  investmentSummary: any = null;
  allocation: any = null;
  performance: any = null;
  analytics: AnalyticsViewModel | null = null;
  advisorAllocation: any = null;
  advisorPerformance: any = null;
  xirr: any = null;
  historical: any = null;
  marketCapAllocation: any = null;
  marketCapAllocationError = '';
  sectorAllocation: any = null;
  sectorAllocationError = '';
  selectedDays = 30;
  selectedPeriod = '30d';
  bestPerformer: AnalyticsInsights['best_performer'] = null;
  worstPerformer: AnalyticsInsights['worst_performer'] = null;
  largestAllocation: AnalyticsInsights['largest_allocation'] = null;
  periodValueChange = 0;

  private historicalChart?: Chart;
  private allocationChart?: Chart;
  private performanceChart?: Chart;
  private advisorChart?: Chart;
  private advisorPerformanceChart?: Chart;
  private marketCapChart?: Chart;
  private sectorChart?: Chart;

  ngOnInit(): void { this.loadAnalytics(); }
  ngAfterViewInit(): void {}

  loadAnalytics(): void {
    this.loading = true;
    this.error = '';
    this.destroyCharts();
    this.marketCapAllocation = null;
    this.marketCapAllocationError = '';
    this.sectorAllocation = null;
    this.sectorAllocationError = '';

    this.wealthApi.getAnalyticsDashboard(this.selectedPeriod, this.selectedDays).subscribe({
      next: data => {
        this.analytics = data;
        this.summary = data.summary;
        this.investmentSummary = data.investment_summary;
        this.allocation = data.allocation;
        this.performance = data.performance;
        this.advisorAllocation = data.advisor_allocation;
        this.advisorPerformance = data.advisor_performance;
        this.xirr = data.xirr;
        this.historical = data.historical;
        this.marketCapAllocation = data.market_cap_allocation;
        this.sectorAllocation = data.sector_allocation;
        this.bestPerformer = data.insights.best_performer;
        this.worstPerformer = data.insights.worst_performer;
        this.largestAllocation = data.insights.largest_allocation;
        this.periodValueChange = data.insights.period_value_change;
        this.loading = false;
        this.cdr.detectChanges();
        setTimeout(() => this.renderCharts(), 0);
      },
      error: error => {
        console.error('Analytics API loading error:', error);
        this.loading = false;
        this.error = 'Unable to load analytics data. Please refresh and try again.';
        this.cdr.detectChanges();
      },
    });
  }

  changePeriod(days: number): void {
    if (this.selectedDays === days) return;
    this.selectedDays = days;
    this.selectedPeriod = this.periodForDays(days);
    this.loadAnalytics();
  }

  changeAnalyticsPeriod(period: string): void {
    if (this.selectedPeriod === period) return;
    this.selectedPeriod = period;
    const days = this.daysForPeriod(period);
    if (days !== null) {
      this.selectedDays = days;
    }
    this.loadAnalytics();
  }

  private daysForPeriod(period: string): number | null {
    switch (period) {
      case '30d': return 30;
      case '90d': return 90;
      case '6m': return 180;
      case '1y': return 365;
      default: return null;
    }
  }

  private periodForDays(days: number): string {
    switch (days) {
      case 90: return '90d';
      case 180: return '6m';
      case 365: return '1y';
      default: return '30d';
    }
  }

  getSelectedPeriodLabel(): string {
    switch (this.selectedPeriod) {
      case 'this-month': return 'This Month';
      case 'last-month': return 'Last Month';
      case 'inception': return 'From Inception';
      case '90d': return '90 Days';
      case '6m': return '6 Months';
      case '1y': return '1 Year';
      default: return '30 Days';
    }
  }

  getSelectedPeriodShortLabel(): string {
    switch (this.selectedPeriod) {
      case 'this-month': return 'this month';
      case 'last-month': return 'last month';
      case 'inception': return 'since inception';
      default: return this.selectedDays + 'd';
    }
  }

  private renderCharts(): void {
    if (this.loading) return;
    this.renderHistoricalChart();
    this.renderAllocationChart();
    this.renderPerformanceChart();
    this.renderAdvisorChart();
    this.renderAdvisorPerformanceChart();
    this.renderMarketCapChart();
    this.renderSectorChart();
  }

  private renderHistoricalChart(): void {
    const canvas = this.historicalChartRef?.nativeElement;
    if (!canvas) return;
    this.historicalChart?.destroy();
    const results = this.historical?.results ?? [];
    if (!results.length) return;
    const config: ChartConfiguration<'line'> = {
      type: 'line',
      data: {
        labels: results.map((item: any) => this.formatDate(item.date)),
        datasets: [
          { label: 'Portfolio Value', data: results.map((item: any) => this.toNumber(item.portfolio_value)), borderColor: this.isDarkTheme() ? '#2fbf8f' : '#111827', backgroundColor: this.isDarkTheme() ? 'rgba(47, 191, 143, 0.10)' : 'rgba(17, 24, 39, 0.07)', borderWidth: 2, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 5 },
          { label: 'Invested Capital', data: results.map((item: any) => this.toNumber(item.invested_value)), borderColor: this.isDarkTheme() ? '#94a3b8' : '#9ca3af', backgroundColor: 'transparent', borderWidth: 2, borderDash: [6, 5], fill: false, tension: 0.35, pointRadius: 0, pointHoverRadius: 5 },
        ],
      },
      options: { animation: { duration: 900, easing: 'easeOutQuart' }, responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { position: 'top', align: 'end', labels: { color: this.chartTextColor() } }, tooltip: { callbacks: { label: (context) => `${context.dataset.label}: ${this.formatCurrency(context.parsed.y ?? 0)}` } } }, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 10, color: this.chartMutedColor() } }, y: { beginAtZero: false, grid: { color: this.chartGridColor() }, ticks: { color: this.chartMutedColor(), callback: (value) => this.formatAxisCurrency(Number(value)) } } } },
    };
    this.historicalChart = new Chart(canvas, config);
  }

  private renderAllocationChart(): void {
    const canvas = this.allocationChartRef?.nativeElement;
    if (!canvas) return;
    this.allocationChart?.destroy();
    const results = this.allocation?.results ?? [];
    if (!results.length) return;
    const labels = results.map((item: any) => this.formatCategory(item.category));
    const values = results.map((item: any) => this.toNumber(item.value));
    const percentages = results.map((item: any) => this.toNumber(item.percentage));
    const config: ChartConfiguration<'doughnut'> = {
      type: 'doughnut',
      data: { labels, datasets: [{ data: values, backgroundColor: results.map((_: any, index: number) => this.swatchColor(index)), borderWidth: 2, borderColor: this.chartBorderColor() }] },
      options: { animation: { duration: 900, easing: 'easeOutQuart' }, responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { position: 'bottom', labels: { color: this.chartTextColor(), usePointStyle: true, padding: 14 } }, tooltip: { callbacks: { label: context => `${context.label}: ${this.formatCurrency(Number(context.raw))} (${(percentages[context.dataIndex] ?? 0).toFixed(2)}%)` } } } },
    };
    this.allocationChart = new Chart(canvas, config);
  }

  private renderPerformanceChart(): void {
    const canvas = this.performanceChartRef?.nativeElement;
    if (!canvas) return;
    this.performanceChart?.destroy();

    const results = this.performance?.results ?? [];
    if (!results.length) return;

    const labels = results.map(
      (item: any) => item.asset_name || item.asset_class || 'Unknown',
    );
    const values = results.map((item: any) => this.toNumber(item.xirr_percentage));

    const config: ChartConfiguration<'bar'> = {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'XIRR %',
          data: values,
          backgroundColor: values.map((value: number) => value >= 0 ? GAIN_COLOR : LOSS_COLOR),
          borderRadius: 5,
          barThickness: 24,
        }],
      },
      options: {
        animation: { duration: 850, easing: 'easeOutQuart' },
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: context => `XIRR: ${this.toNumber(context.parsed.x).toFixed(2)}%`,
            },
          },
        },
        scales: {
          x: {
            ticks: {
              color: this.chartMutedColor(),
              callback: value => `${Number(value).toFixed(0)}%`,
            },
            grid: { color: this.chartGridColor() },
          },
          y: {
            ticks: { color: this.chartMutedColor() },
            grid: { display: false },
          },
        },
      },
    };

    this.performanceChart = new Chart(canvas, config);
  }

  private renderAdvisorChart(): void {
    const canvas = this.advisorChartRef?.nativeElement;
    if (!canvas) return;
    this.advisorChart?.destroy();
    const results = this.advisorAllocation?.results ?? [];
    if (!results.length) return;
    const labels = results.map((item: any) => item.advisor || 'Unassigned');
    const values = results.map((item: any) => this.toNumber(item.value));
    const percentages = results.map((item: any) => this.toNumber(item.percentage));
    const config: ChartConfiguration<'pie'> = { type: 'pie', data: { labels, datasets: [{ data: values, backgroundColor: results.map((item: any) => this.advisorColor(item.advisor || 'Unassigned')), borderWidth: 2, borderColor: this.chartBorderColor() }] }, options: { animation: { duration: 900, easing: 'easeOutQuart' }, responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { color: this.chartTextColor(), usePointStyle: true, padding: 14 } }, tooltip: { callbacks: { label: context => `${context.label}: ${this.formatCurrency(Number(context.raw))} (${(percentages[context.dataIndex] ?? 0).toFixed(2)}%)` } } } } };
    this.advisorChart = new Chart(canvas, config);
  }

  private renderAdvisorPerformanceChart(): void {
    const canvas = this.advisorPerformanceChartRef?.nativeElement;
    if (!canvas) return;
    this.advisorPerformanceChart?.destroy();
    const results = this.advisorPerformance?.results ?? [];
    if (!results.length) return;
    const sortedResults = [...results].sort((a: any, b: any) => this.toNumber(b.pnl_percentage) - this.toNumber(a.pnl_percentage));
    const labels = sortedResults.map((item: any) => item.advisor || 'Unassigned');
    const values = sortedResults.map((item: any) => this.toNumber(item.pnl_percentage));
    const config: ChartConfiguration<'bar'> = { type: 'bar', data: { labels, datasets: [{ label: 'Return %', data: values, backgroundColor: values.map(value => value >= 0 ? GAIN_COLOR : LOSS_COLOR), borderRadius: 5, barThickness: 24 }] }, options: { animation: { duration: 850, easing: 'easeOutQuart' }, indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: context => `Return: ${this.toNumber(context.parsed.x).toFixed(2)}%` } } }, scales: { x: { ticks: { color: this.chartMutedColor(), callback: value => `${Number(value).toFixed(0)}%` }, grid: { color: this.chartGridColor() } }, y: { ticks: { color: this.chartMutedColor() }, grid: { display: false } } } } };
    this.advisorPerformanceChart = new Chart(canvas, config);
  }

  private renderMarketCapChart(): void {
    const canvas = this.marketCapChartRef?.nativeElement;
    if (!canvas) return;
    this.marketCapChart?.destroy();
    const results = this.marketCapAllocation?.results ?? [];
    if (!results.length) return;
    const labels = results.map((item: any) => item.cap_type);
    const values = results.map((item: any) => Number(item.current_value));
    const percentages = results.map((item: any) => Number(item.percentage));
    const config: ChartConfiguration<'doughnut'> = { type: 'doughnut', data: { labels, datasets: [{ data: values, backgroundColor: results.map((_: any, index: number) => this.swatchColor(index)), borderWidth: 2, borderColor: this.chartBorderColor() }] }, options: { animation: { duration: 900, easing: 'easeOutQuart' }, responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { position: 'bottom', labels: { color: this.chartTextColor(), usePointStyle: true, padding: 16 } }, tooltip: { callbacks: { label: context => `${context.label}: ${this.formatCurrency(Number(context.raw))} (${(percentages[context.dataIndex] ?? 0).toFixed(2)}%)` } } } } };
    this.marketCapChart = new Chart(canvas, config);
  }

  private renderSectorChart(): void {
    const canvas = this.sectorChartRef?.nativeElement;
    if (!canvas) return;
    this.sectorChart?.destroy();
    const results = this.sectorAllocation?.results ?? [];
    if (!results.length) return;
    const labels = results.map((item: any) => item.sector);
    const values = results.map((item: any) => this.toNumber(item.current_value));
    const percentages = results.map((item: any) => this.toNumber(item.percentage));
    const config: ChartConfiguration<'doughnut'> = { type: 'doughnut', data: { labels, datasets: [{ data: values, backgroundColor: results.map((_: any, index: number) => this.swatchColor(index)), borderWidth: 2, borderColor: this.chartBorderColor() }] }, options: { animation: { duration: 900, easing: 'easeOutQuart' }, responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { position: 'bottom', labels: { color: this.chartTextColor(), usePointStyle: true, padding: 14 } }, tooltip: { callbacks: { label: context => `${context.label}: ${this.formatAxisCurrency(Number(context.raw))} (${(percentages[context.dataIndex] ?? 0).toFixed(2)}%)` } } } } };
    this.sectorChart = new Chart(canvas, config);
  }

  private destroyCharts(): void {
    this.historicalChart?.destroy();
    this.allocationChart?.destroy();
    this.performanceChart?.destroy();
    this.advisorChart?.destroy();
    this.advisorPerformanceChart?.destroy();
    this.marketCapChart?.destroy();
    this.sectorChart?.destroy();
    this.historicalChart = undefined;
    this.allocationChart = undefined;
    this.performanceChart = undefined;
    this.advisorChart = undefined;
    this.advisorPerformanceChart = undefined;
    this.marketCapChart = undefined;
    this.sectorChart = undefined;
  }

  ngOnDestroy(): void { this.destroyCharts(); }
  private toNumber(value: any): number { const number = Number(value); return Number.isFinite(number) ? number : 0; }
  private isDarkTheme(): boolean { return document.documentElement.classList.contains('dark-theme'); }
  private chartTextColor(): string { return this.isDarkTheme() ? '#cbd5e1' : '#475467'; }
  private chartMutedColor(): string { return this.isDarkTheme() ? '#8a93a6' : '#667085'; }
  private chartGridColor(): string { return this.isDarkTheme() ? '#2a2e38' : '#e5e7eb'; }
  private chartBorderColor(): string { return this.isDarkTheme() ? '#cbd5e1' : '#ffffff'; }
  private categoryPalette(): string[] { return this.isDarkTheme() ? CATEGORY_PALETTE_DARK : CATEGORY_PALETTE_LIGHT; }
  formatCurrency(value: number): string { return `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`; }
  swatchColor(index: number): string { const palette = this.categoryPalette(); return palette[index % palette.length]; }
  advisorColor(name: string): string { const value = (name || 'Unassigned').trim() || 'Unassigned'; let hash = 0; for (let i = 0; i < value.length; i++) hash = (hash * 31 + value.charCodeAt(i)) >>> 0; const palette = this.categoryPalette(); return palette[hash % palette.length]; }
  advisorInitials(name: string): string { const value = (name || 'Unassigned').trim() || 'Unassigned'; const words = value.split(/\s+/).filter(Boolean); return words.length === 1 ? words[0].slice(0, 2).toUpperCase() : (words[0][0] + words[words.length - 1][0]).toUpperCase(); }
  formatAxisCurrency(value: number): string { const absolute = Math.abs(value); if (absolute >= 10000000) return `₹${(value / 10000000).toFixed(1)}Cr`; if (absolute >= 100000) return `₹${(value / 100000).toFixed(1)}L`; if (absolute >= 1000) return `₹${(value / 1000).toFixed(0)}K`; return `₹${value}`; }
  formatDate(value: string): string { if (!value) return ''; const date = new Date(`${value}T00:00:00`); return date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }); }
  formatCategory(value: string): string { if (!value) return 'Unknown'; return value.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, char => char.toUpperCase()); }
  getBestPerformerName(): string {
    if (!this.bestPerformer) return '-';
    return this.bestPerformer.asset_name || this.bestPerformer.asset_class || 'Unknown';
  }
  getWorstPerformerName(): string {
    if (!this.worstPerformer) return '-';
    return this.worstPerformer.asset_name || this.worstPerformer.asset_class || 'Unknown';
  }
  getBestPerformerReturn(): number { return this.toNumber(this.bestPerformer?.xirr_percentage); }
  getWorstPerformerReturn(): number { return this.toNumber(this.worstPerformer?.xirr_percentage); }
  getLargestAllocationName(): string { if (!this.largestAllocation) return '-'; return this.formatCategory(this.largestAllocation.category); }
  getLargestAllocationPercentage(): number { return this.toNumber(this.largestAllocation?.percentage); }
}
