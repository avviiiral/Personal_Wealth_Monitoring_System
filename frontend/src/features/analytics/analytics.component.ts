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

import { forkJoin } from 'rxjs';

import { WealthApiService } from '../../core/services/wealth-api.service';
import { PortfolioApiService, PortfolioTreeResponse } from '../../core/services/portfolio-api.service';

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
  private readonly portfolioApi = inject(PortfolioApiService);
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
  portfolioTree: PortfolioTreeResponse | null = null;
  advisorAllocation: any = null;
  advisorPerformance: any = null;
  xirr: any = null;
  historical: any = null;
  marketCapAllocation: any = null;
  marketCapAllocationError = '';
  sectorAllocation: any = null;
  sectorAllocationError = '';
  selectedDays = 30;
  bestPerformer: any = null;
  worstPerformer: any = null;
  largestAllocation: any = null;
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
    this.performance = null;
    this.portfolioTree = null;

    this.portfolioApi.getPortfolioTree().subscribe({
      next: tree => {
        this.portfolioTree = tree;
        this.performance = {
          results: this.getInvestmentPerformanceRows(),
        };
        this.calculateInsights();
        this.cdr.markForCheck();
        setTimeout(() => {
          this.renderPerformanceChart();
          this.cdr.markForCheck();
        });
      },
      error: error => {
        console.error('PORTFOLIO TREE API ERROR:', error);
        this.performance = { results: [] };
        this.cdr.markForCheck();
      },
    });

    this.wealthApi.getMarketCapAllocation().subscribe({
      next: data => {
        this.marketCapAllocation = data;
        this.cdr.markForCheck();
        setTimeout(() => { this.renderMarketCapChart(); this.cdr.markForCheck(); });
      },
      error: error => {
        console.error('MARKET CAP ALLOCATION API ERROR:', error);
        this.marketCapAllocationError = 'Unable to load market cap allocation.';
        this.cdr.markForCheck();
      },
    });

    this.wealthApi.getSectorAllocation().subscribe({
      next: data => {
        this.sectorAllocation = data;
        this.cdr.markForCheck();
        setTimeout(() => { this.renderSectorChart(); this.cdr.markForCheck(); });
      },
      error: error => {
        console.error('SECTOR ALLOCATION API ERROR:', error);
        this.sectorAllocationError = 'Unable to load sector allocation.';
        this.cdr.markForCheck();
      },
    });

    forkJoin({
      summary: this.wealthApi.getSummary(),
      investmentSummary: this.wealthApi.getInvestmentSummary(),
      allocation: this.wealthApi.getAllocation(),
      advisorAllocation: this.wealthApi.getAllocationByAdvisor(),
      advisorPerformance: this.wealthApi.getPerformanceByAdvisor(),
      historical: this.wealthApi.getHistorical(this.selectedDays),
    }).subscribe({
      next: data => {
        console.log('Analytics API response:', data);
        try {
          this.summary = data.summary;
          this.investmentSummary = data.investmentSummary;
          this.allocation = data.allocation;
          this.advisorAllocation = data.advisorAllocation;
          this.advisorPerformance = data.advisorPerformance;
          this.xirr = { xirr_percentage: this.summary?.xirr_percentage ?? null };
          this.historical = data.historical;
          this.calculateInsights();
        } catch (processingError) {
          console.error('Analytics response processing error:', processingError);
          this.error = 'Analytics data was received, but could not be processed.';
        } finally {
          this.loading = false;
          this.cdr.detectChanges();
          setTimeout(() => this.renderCharts(), 0);
        }
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
    this.loadAnalytics();
  }

  private calculateInsights(): void {
    const performanceResults = this.performance?.results ?? [];
    if (performanceResults.length) {
      const sorted = [...performanceResults].sort((a: any, b: any) => this.toNumber(b.pnl_percentage) - this.toNumber(a.pnl_percentage));
      this.bestPerformer = sorted[0];
      this.worstPerformer = sorted[sorted.length - 1];
    } else {
      this.bestPerformer = null;
      this.worstPerformer = null;
    }
    const allocationResults = this.allocation?.results ?? [];
    this.largestAllocation = allocationResults.length
      ? [...allocationResults].sort((a: any, b: any) => this.toNumber(b.percentage) - this.toNumber(a.percentage))[0]
      : null;
    const historicalResults = this.historical?.results ?? [];
    if (historicalResults.length >= 2) {
      const first = this.toNumber(historicalResults[0].portfolio_value);
      const last = this.toNumber(historicalResults[historicalResults.length - 1].portfolio_value);
      this.periodValueChange = first > 0 ? ((last - first) / first) * 100 : 0;
    } else {
      this.periodValueChange = 0;
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

  private getInvestmentPerformanceRows(): Array<{
    asset_name: string;
    asset_class: string;
    xirr_percentage: number;
  }> {
    const rowsByKey = new Map<string, {
      asset_name: string;
      asset_class: string;
      xirr_percentage: number;
    }>();

    for (const family of this.portfolioTree?.families ?? []) {
      for (const portfolio of family.portfolios ?? []) {
        for (const assetClass of portfolio.asset_classes ?? []) {
          for (const subClass of assetClass.sub_classes ?? []) {
            for (const asset of subClass.assets ?? []) {
              const xirr = Number(asset.asset_name_xirr);

              if (!Number.isFinite(xirr)) {
                continue;
              }

              const assetName = asset.asset_name?.trim() || 'Unnamed Asset';
              const subClassName = subClass.sub_class?.trim() || 'Unassigned';
              const key = `${subClassName}::${assetName}`;

              if (!rowsByKey.has(key)) {
                rowsByKey.set(key, {
                  asset_name: assetName,
                  asset_class: subClassName,
                  xirr_percentage: xirr,
                });
              }
            }
          }
        }
      }
    }

    return Array.from(rowsByKey.values()).sort(
      (a, b) => b.xirr_percentage - a.xirr_percentage,
    );
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
  getBestPerformerName(): string { if (!this.bestPerformer) return '-'; return this.bestPerformer.asset_class || this.bestPerformer.symbol || this.bestPerformer.asset_name || this.bestPerformer.scheme_name || this.bestPerformer.name || 'Unknown'; }
  getWorstPerformerName(): string { if (!this.worstPerformer) return '-'; return this.worstPerformer.asset_class || this.worstPerformer.symbol || this.worstPerformer.asset_name || this.worstPerformer.scheme_name || this.worstPerformer.name || 'Unknown'; }
  getBestPerformerReturn(): number { return this.toNumber(this.bestPerformer?.pnl_percentage); }
  getWorstPerformerReturn(): number { return this.toNumber(this.worstPerformer?.pnl_percentage); }
  getLargestAllocationName(): string { if (!this.largestAllocation) return '-'; return this.formatCategory(this.largestAllocation.category); }
  getLargestAllocationPercentage(): number { return this.toNumber(this.largestAllocation?.percentage); }
}
