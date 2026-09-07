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

Chart.register(...registerables);

// Shared categorical palette for the Analytics page — used for the
// Allocation / Advisor pie & doughnut charts, their matching row
// swatches, and the advisor initials chips. Keeping one palette used
// everywhere means a color always means the same category or advisor
// across every chart and list on the page.
const CATEGORY_PALETTE = [
  '#111827', // ink
  '#9c6b1f', // brass
  '#0f6f66', // teal
  '#3b5478', // slate
  '#6d4a6b', // plum
  '#8a5a3b', // umber
  '#4b5563', // graphite
  '#7a3742', // deep wine
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

  @ViewChild('historicalChart')
  historicalChartRef?: ElementRef<HTMLCanvasElement>;

  @ViewChild('allocationChart')
  allocationChartRef?: ElementRef<HTMLCanvasElement>;

  @ViewChild('performanceChart')
  performanceChartRef?: ElementRef<HTMLCanvasElement>;

  @ViewChild('advisorChart')
  advisorChartRef?: ElementRef<HTMLCanvasElement>;

  @ViewChild('advisorPerformanceChart')
  advisorPerformanceChartRef?: ElementRef<HTMLCanvasElement>;

  @ViewChild('marketCapChart')
  marketCapChartRef?: ElementRef<HTMLCanvasElement>;

  @ViewChild('nonStockHoldingTypesChart')
  nonStockHoldingTypesChartRef?: ElementRef<HTMLCanvasElement>;

  @ViewChild('sectorChart')
  sectorChartRef?: ElementRef<HTMLCanvasElement>;

  loading = true;
  error = '';

  summary: any = null;
  investmentSummary: any = null;
  allocation: any = null;
  performance: any = null;
  advisorAllocation: any = null;
  advisorPerformance: any = null;
  xirr: any = null;
  historical: any = null;

  marketCapAllocation: any = null;
  marketCapAllocationError = '';

  nonStockHoldingTypes: any = null;
  nonStockHoldingTypesError = '';

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
  private nonStockHoldingTypesChart?: Chart;
  private sectorChart?: Chart;

  ngOnInit(): void {
    this.loadAnalytics();
  }

  ngAfterViewInit(): void {
    // Charts are rendered after API data is loaded
    // and Angular has created the canvas elements.
  }

  loadAnalytics(): void {
    this.loading = true;
    this.error = '';

    this.destroyCharts();

    // MARKET CAP ALLOCATION
    // (loaded independently of the forkJoin below so a failure here
    // doesn't block the rest of the Analytics page from loading)
    this.marketCapAllocation = null;
    this.marketCapAllocationError = '';

    this.wealthApi.getMarketCapAllocation().subscribe({
      next: (data) => {
        this.marketCapAllocation = data;

        this.cdr.markForCheck();

        setTimeout(() => {
          this.renderMarketCapChart();
          this.cdr.markForCheck();
        });
      },

      error: (error) => {
        console.error('MARKET CAP ALLOCATION API ERROR:', error);

        this.marketCapAllocationError = 'Unable to load market cap allocation.';

        this.cdr.markForCheck();
      },
    });

    // NON-STOCK HOLDING TYPES
    this.nonStockHoldingTypes = null;
    this.nonStockHoldingTypesError = '';

    this.wealthApi.getNonStockHoldingTypes().subscribe({
      next: (data) => {
        this.nonStockHoldingTypes = data;

        this.cdr.markForCheck();

        setTimeout(() => {
          this.renderNonStockHoldingTypesChart();
          this.cdr.markForCheck();
        });
      },

      error: (error) => {
        console.error('NON-STOCK HOLDING TYPES API ERROR:', error);

        this.nonStockHoldingTypesError = 'Unable to load holding type breakdown.';

        this.cdr.markForCheck();
      },
    });

    // SECTOR ALLOCATION
    this.sectorAllocation = null;
    this.sectorAllocationError = '';

    this.wealthApi.getSectorAllocation().subscribe({
      next: (data) => {
        this.sectorAllocation = data;

        this.cdr.markForCheck();

        setTimeout(() => {
          this.renderSectorChart();
          this.cdr.markForCheck();
        });
      },

      error: (error) => {
        console.error('SECTOR ALLOCATION API ERROR:', error);

        this.sectorAllocationError = 'Unable to load sector allocation.';

        this.cdr.markForCheck();
      },
    });

    forkJoin({
      summary: this.wealthApi.getSummary(),
      investmentSummary: this.wealthApi.getInvestmentSummary(),
      performance: this.wealthApi.getPerformanceBySubclass(),
      advisorAllocation: this.wealthApi.getAllocationByAdvisor(),
      advisorPerformance: this.wealthApi.getPerformanceByAdvisor(),
      historical: this.wealthApi.getHistorical(this.selectedDays),
    }).subscribe({
      next: (data) => {
        console.log('Analytics API response:', data);

        try {
          this.summary = data.summary;
          this.investmentSummary = data.investmentSummary;
          this.allocation = { results: this.allocationByCategory };
          this.performance = data.performance;
          this.advisorAllocation = data.advisorAllocation;
          this.advisorPerformance = data.advisorPerformance;
          this.xirr = {
            xirr_percentage: this.summary?.xirr_percentage ?? null,
          };
          this.historical = data.historical;

          console.log('Analytics summary:', this.summary);
          console.log('Analytics investment summary:', this.investmentSummary);
          console.log('Analytics allocation:', this.allocation);
          console.log('Analytics performance:', this.performance);
          console.log('Analytics advisor allocation:', this.advisorAllocation);
          console.log('Analytics advisor performance:', this.advisorPerformance);
          console.log('Analytics xirr:', this.xirr);
          console.log('Analytics historical:', this.historical);

          this.calculateInsights();

          console.log('Analytics insights calculated successfully');
        } catch (processingError) {
          console.error('Analytics response processing error:', processingError);

          this.error = 'Analytics data was received, but could not be processed.';
        } finally {
          /*
           * Important:
           * loading must become false before trying to access
           * the canvas elements controlled by *ngIf.
           */
          this.loading = false;

          /*
           * Immediately tell Angular that the loading state changed.
           * This allows the chart section to be created in the DOM.
           */
          this.cdr.detectChanges();

          /*
           * Wait one tick so @ViewChild canvas references exist.
           */
          setTimeout(() => {
            this.renderCharts();
          }, 0);
        }
      },

      error: (error) => {
        console.error('Analytics API loading error:', error);

        this.loading = false;

        this.error = 'Unable to load analytics data. Please refresh and try again.';

        this.cdr.detectChanges();
      },
    });
  }

  changePeriod(days: number): void {
    if (this.selectedDays === days) {
      return;
    }

    this.selectedDays = days;

    this.loadAnalytics();
  }

  /**
   * Groups the Investment Summary rows by Asset Category, summing
   * current value and % of total, so the Analytics Allocation chart
   * shows the exact same categorization and totals as the
   * Dashboard's Investment Summary table — one source of truth for
   * both.
   */
  private get allocationByCategory(): Array<{
    category: string;
    value: number;
    percentage: number;
  }> {
    const results = this.investmentSummary?.results ?? [];

    const order: string[] = [];
    const totals = new Map<
      string,
      {
        value: number;
        percentage: number;
      }
    >();

    for (const row of results) {
      const category = row.asset_category;

      if (!totals.has(category)) {
        totals.set(category, {
          value: 0,
          percentage: 0,
        });

        order.push(category);
      }

      const entry = totals.get(category)!;

      entry.value += this.toNumber(row.current_value);
      entry.percentage += this.toNumber(row.percentage_of_total);
    }

    return order
      .map((category) => {
        const entry = totals.get(category)!;

        return {
          category,
          value: entry.value,
          percentage: Math.round(entry.percentage * 100) / 100,
        };
      })
      .filter((entry) => entry.value > 0);
  }

  private calculateInsights(): void {
    const performanceResults = this.performance?.results ?? [];

    if (performanceResults.length) {
      const sorted = [...performanceResults].sort(
        (a: any, b: any) => this.toNumber(b.pnl_percentage) - this.toNumber(a.pnl_percentage),
      );

      this.bestPerformer = sorted[0];
      this.worstPerformer = sorted[sorted.length - 1];
    } else {
      this.bestPerformer = null;
      this.worstPerformer = null;
    }

    const allocationResults = this.allocation?.results ?? [];

    if (allocationResults.length) {
      this.largestAllocation = [...allocationResults].sort(
        (a: any, b: any) => this.toNumber(b.percentage) - this.toNumber(a.percentage),
      )[0];
    } else {
      this.largestAllocation = null;
    }

    const historicalResults = this.historical?.results ?? [];

    if (historicalResults.length >= 2) {
      const first = this.toNumber(historicalResults[0].portfolio_value);

      const last = this.toNumber(historicalResults[historicalResults.length - 1].portfolio_value);

      if (first > 0) {
        this.periodValueChange = ((last - first) / first) * 100;
      } else {
        this.periodValueChange = 0;
      }
    } else {
      this.periodValueChange = 0;
    }
  }

  private renderCharts(): void {
    if (this.loading) {
      return;
    }

    this.renderHistoricalChart();
    this.renderAllocationChart();
    this.renderPerformanceChart();
    this.renderAdvisorChart();
    this.renderAdvisorPerformanceChart();
    this.renderMarketCapChart();
    this.renderNonStockHoldingTypesChart();
    this.renderSectorChart();
  }

  private renderHistoricalChart(): void {
    const canvas = this.historicalChartRef?.nativeElement;

    if (!canvas) {
      console.warn('Historical chart canvas not available.');
      return;
    }

    this.historicalChart?.destroy();

    const results = this.historical?.results ?? [];

    if (!results.length) {
      console.warn('No historical data available for wealth chart.');
      return;
    }

    const labels = results.map((item: any) => this.formatDate(item.date));

    const portfolioValues = results.map((item: any) => this.toNumber(item.portfolio_value));

    const investedValues = results.map((item: any) => this.toNumber(item.invested_value));

    const config: ChartConfiguration<'line'> = {
      type: 'line',

      data: {
        labels,

        datasets: [
          {
            label: 'Portfolio Value',
            data: portfolioValues,

            borderColor: '#111827',
            backgroundColor: 'rgba(17, 24, 39, 0.07)',

            borderWidth: 2,
            fill: true,
            tension: 0.35,

            pointRadius: 0,
            pointHoverRadius: 5,
          },

          {
            label: 'Invested Capital',
            data: investedValues,

            borderColor: '#9ca3af',
            backgroundColor: 'transparent',

            borderWidth: 2,
            borderDash: [6, 5],

            fill: false,
            tension: 0.35,

            pointRadius: 0,
            pointHoverRadius: 5,
          },
        ],
      },

      options: {
        responsive: true,
        maintainAspectRatio: false,

        interaction: {
          mode: 'index',
          intersect: false,
        },

        plugins: {
          legend: {
            position: 'top',
            align: 'end',
          },

          tooltip: {
            callbacks: {
              label: (context) => {
                const value = context.parsed.y ?? 0;

                return `${context.dataset.label}: ${this.formatCurrency(value)}`;
              },
            },
          },
        },

        scales: {
          x: {
            grid: {
              display: false,
            },

            ticks: {
              maxTicksLimit: 10,
            },
          },

          y: {
            beginAtZero: false,

            ticks: {
              callback: (value) => this.formatAxisCurrency(Number(value)),
            },
          },
        },
      },
    };

    this.historicalChart = new Chart(canvas, config);
  }

  private renderAllocationChart(): void {
    const canvas = this.allocationChartRef?.nativeElement;

    if (!canvas) {
      console.warn('Allocation chart canvas not available.');
      return;
    }

    this.allocationChart?.destroy();

    const results = this.allocation?.results ?? [];

    if (!results.length) {
      console.warn('No allocation data available.');
      return;
    }

    const labels = results.map((item: any) => this.formatCategory(item.category));

    const values = results.map((item: any) => this.toNumber(item.value));

    const percentages = results.map((item: any) => this.toNumber(item.percentage));

    const config: ChartConfiguration<'doughnut'> = {
      type: 'doughnut',

      data: {
        labels,

        datasets: [
          {
            data: values,

            backgroundColor: results.map((_: any, index: number) =>
              this.swatchColor(index),
            ),

            borderWidth: 2,
            borderColor: '#ffffff',
          },
        ],
      },

      options: {
        responsive: true,
        maintainAspectRatio: false,

        cutout: '68%',

        plugins: {
          legend: {
            position: 'bottom',

            labels: {
              usePointStyle: true,
              padding: 14,
            },
          },

          tooltip: {
            callbacks: {
              label: (context) => {
                const index = context.dataIndex;

                const percentage = percentages[index] ?? 0;

                return `${context.label}: ${this.formatCurrency(
                  Number(context.raw),
                )} (${percentage.toFixed(2)}%)`;
              },
            },
          },
        },
      },
    };

    this.allocationChart = new Chart(canvas, config);
  }

  private renderPerformanceChart(): void {
    const canvas = this.performanceChartRef?.nativeElement;

    if (!canvas) {
      console.warn('Performance chart canvas not available.');
      return;
    }

    this.performanceChart?.destroy();

    const results = this.performance?.results ?? [];

    if (!results.length) {
      console.warn('No performance data available.');
      return;
    }

    const sortedResults = [...results].sort(
      (a: any, b: any) => this.toNumber(b.pnl_percentage) - this.toNumber(a.pnl_percentage),
    );

    const labels = sortedResults.map(
      (item: any) =>
        item.asset_class || item.symbol || item.asset_name || item.scheme_name || item.name || 'Unknown',
    );

    const values = sortedResults.map((item: any) => this.toNumber(item.pnl_percentage));

    const backgroundColors = values.map((value) => (value >= 0 ? GAIN_COLOR : LOSS_COLOR));

    const config: ChartConfiguration<'bar'> = {
      type: 'bar',

      data: {
        labels,

        datasets: [
          {
            label: 'Return %',
            data: values,

            backgroundColor: backgroundColors,

            borderRadius: 5,
            barThickness: 24,
          },
        ],
      },

      options: {
        indexAxis: 'y',

        responsive: true,
        maintainAspectRatio: false,

        plugins: {
          legend: {
            display: false,
          },

          tooltip: {
            callbacks: {
              label: (context) => `Return: ${this.toNumber(context.parsed.x).toFixed(2)}%`,
            },
          },
        },

        scales: {
          x: {
            ticks: {
              callback: (value) => `${Number(value).toFixed(0)}%`,
            },

            grid: {
              color: '#eef0f3',
            },
          },

          y: {
            grid: {
              display: false,
            },
          },
        },
      },
    };

    this.performanceChart = new Chart(canvas, config);
  }

  private renderAdvisorChart(): void {
    const canvas = this.advisorChartRef?.nativeElement;

    if (!canvas) {
      console.warn('Advisor chart canvas not available.');
      return;
    }

    this.advisorChart?.destroy();

    const results = this.advisorAllocation?.results ?? [];

    if (!results.length) {
      console.warn('No advisor allocation data available.');
      return;
    }

    const labels = results.map((item: any) => item.advisor || 'Unassigned');

    const values = results.map((item: any) => this.toNumber(item.value));

    const percentages = results.map((item: any) => this.toNumber(item.percentage));

    const config: ChartConfiguration<'pie'> = {
      type: 'pie',

      data: {
        labels,

        datasets: [
          {
            data: values,

            backgroundColor: results.map((item: any) =>
              this.advisorColor(item.advisor || 'Unassigned'),
            ),

            borderWidth: 2,
            borderColor: '#ffffff',
          },
        ],
      },

      options: {
        responsive: true,
        maintainAspectRatio: false,

        plugins: {
          legend: {
            position: 'bottom',

            labels: {
              usePointStyle: true,
              padding: 14,
            },
          },

          tooltip: {
            callbacks: {
              label: (context) => {
                const index = context.dataIndex;

                const percentage = percentages[index] ?? 0;

                return `${context.label}: ${this.formatCurrency(
                  Number(context.raw),
                )} (${percentage.toFixed(2)}%)`;
              },
            },
          },
        },
      },
    };

    this.advisorChart = new Chart(canvas, config);
  }

  private renderAdvisorPerformanceChart(): void {
    const canvas = this.advisorPerformanceChartRef?.nativeElement;

    if (!canvas) {
      console.warn('Advisor performance chart canvas not available.');
      return;
    }

    this.advisorPerformanceChart?.destroy();

    const results = this.advisorPerformance?.results ?? [];

    if (!results.length) {
      console.warn('No advisor performance data available.');
      return;
    }

    const sortedResults = [...results].sort(
      (a: any, b: any) => this.toNumber(b.pnl_percentage) - this.toNumber(a.pnl_percentage),
    );

    const labels = sortedResults.map((item: any) => item.advisor || 'Unassigned');

    const values = sortedResults.map((item: any) => this.toNumber(item.pnl_percentage));

    const backgroundColors = values.map((value) => (value >= 0 ? GAIN_COLOR : LOSS_COLOR));

    const config: ChartConfiguration<'bar'> = {
      type: 'bar',

      data: {
        labels,

        datasets: [
          {
            label: 'Return %',
            data: values,

            backgroundColor: backgroundColors,

            borderRadius: 5,
            barThickness: 24,
          },
        ],
      },

      options: {
        indexAxis: 'y',

        responsive: true,
        maintainAspectRatio: false,

        plugins: {
          legend: {
            display: false,
          },

          tooltip: {
            callbacks: {
              label: (context) => `Return: ${this.toNumber(context.parsed.x).toFixed(2)}%`,
            },
          },
        },

        scales: {
          x: {
            ticks: {
              callback: (value) => `${Number(value).toFixed(0)}%`,
            },

            grid: {
              color: '#eef0f3',
            },
          },

          y: {
            grid: {
              display: false,
            },
          },
        },
      },
    };

    this.advisorPerformanceChart = new Chart(canvas, config);
  }

  private renderMarketCapChart(): void {
    const canvas = this.marketCapChartRef?.nativeElement;

    if (!canvas) {
      console.warn('Market cap chart canvas not available.');
      return;
    }

    this.marketCapChart?.destroy();

    const results = this.marketCapAllocation?.results ?? [];

    if (!results.length) {
      console.warn('No market cap allocation data available.');
      return;
    }

    const labels = results.map((item: any) => item.cap_type);

    const values = results.map((item: any) => Number(item.current_value));

    const percentages = results.map((item: any) => Number(item.percentage));

    const config: ChartConfiguration<'doughnut'> = {
      type: 'doughnut',

      data: {
        labels,

        datasets: [
          {
            data: values,

            backgroundColor: results.map((_: any, index: number) =>
              this.swatchColor(index),
            ),

            borderWidth: 2,
            borderColor: '#ffffff',
          },
        ],
      },

      options: {
        responsive: true,
        maintainAspectRatio: false,

        cutout: '68%',

        plugins: {
          legend: {
            position: 'bottom',

            labels: {
              usePointStyle: true,
              padding: 16,
            },
          },

          tooltip: {
            callbacks: {
              label: (context) => {
                const index = context.dataIndex;
                const percentage = percentages[index] ?? 0;

                return `${context.label}: ${this.formatCurrency(
                  Number(context.raw),
                )} (${percentage.toFixed(2)}%)`;
              },
            },
          },
        },
      },
    };

    this.marketCapChart = new Chart(canvas, config);
  }

  private renderNonStockHoldingTypesChart(): void {
    const canvas = this.nonStockHoldingTypesChartRef?.nativeElement;

    if (!canvas) {
      console.warn('Non-stock holding types chart canvas not available.');
      return;
    }

    this.nonStockHoldingTypesChart?.destroy();

    const results = this.nonStockHoldingTypes?.results ?? [];

    if (!results.length) {
      console.warn('No non-stock holding type data available.');
      return;
    }

    const labels = results.map((item: any) => item.holding_type);

    const values = results.map((item: any) => Number(item.current_value));

    const percentages = results.map((item: any) => Number(item.percentage));

    const config: ChartConfiguration<'doughnut'> = {
      type: 'doughnut',

      data: {
        labels,

        datasets: [
          {
            data: values,

            backgroundColor: [
              '#085888',
              '#fd7740',
              '#cc9f53',
              '#0f7a5c',
              '#7c3aed',
              '#dc2626',
              '#0891b2',
              '#65a30d',
              '#c026d3',
              '#475569',
              '#e2e8f0',
              '#94a3b8',
            ],

            borderWidth: 2,
            borderColor: '#ffffff',
          },
        ],
      },

      options: {
        responsive: true,
        maintainAspectRatio: false,

        cutout: '68%',

        plugins: {
          legend: {
            position: 'bottom',

            labels: {
              usePointStyle: true,
              padding: 16,
            },
          },

          tooltip: {
            callbacks: {
              label: (context) => {
                const index = context.dataIndex;
                const percentage = percentages[index] ?? 0;

                return `${context.label}: ${this.formatCurrency(
                  Number(context.raw),
                )} (${percentage.toFixed(2)}%)`;
              },
            },
          },
        },
      },
    };

    this.nonStockHoldingTypesChart = new Chart(canvas, config);
  }

  private renderSectorChart(): void {
    const canvas = this.sectorChartRef?.nativeElement;

    if (!canvas) {
      console.warn('Sector chart canvas not available.');
      return;
    }

    this.sectorChart?.destroy();

    const results = this.sectorAllocation?.results ?? [];

    if (!results.length) {
      console.warn('No sector allocation data available.');
      return;
    }

    const labels = results.map((item: any) => item.sector);

    const values = results.map((item: any) => this.toNumber(item.current_value));

    const percentages = results.map((item: any) => this.toNumber(item.percentage));

    const config: ChartConfiguration<'doughnut'> = {
      type: 'doughnut',

      data: {
        labels,

        datasets: [
          {
            data: values,

            backgroundColor: results.map((_: any, index: number) =>
              this.swatchColor(index),
            ),

            borderWidth: 2,
            borderColor: '#ffffff',
          },
        ],
      },

      options: {
        responsive: true,
        maintainAspectRatio: false,

        cutout: '68%',

        plugins: {
          legend: {
            position: 'bottom',

            labels: {
              usePointStyle: true,
              padding: 14,
            },
          },

          tooltip: {
            callbacks: {
              label: (context) => {
                const index = context.dataIndex;
                const percentage = percentages[index] ?? 0;

                return `${context.label}: ${this.formatAxisCurrency(
                  Number(context.raw),
                )} (${percentage.toFixed(2)}%)`;
              },
            },
          },
        },
      },
    };

    this.sectorChart = new Chart(canvas, config);
  }

  private destroyCharts(): void {
    this.historicalChart?.destroy();
    this.allocationChart?.destroy();
    this.performanceChart?.destroy();
    this.advisorChart?.destroy();
    this.advisorPerformanceChart?.destroy();
    this.marketCapChart?.destroy();
    this.nonStockHoldingTypesChart?.destroy();
    this.sectorChart?.destroy();

    this.historicalChart = undefined;
    this.allocationChart = undefined;
    this.performanceChart = undefined;
    this.advisorChart = undefined;
    this.advisorPerformanceChart = undefined;
    this.marketCapChart = undefined;
    this.nonStockHoldingTypesChart = undefined;
    this.sectorChart = undefined;
  }

  ngOnDestroy(): void {
    this.destroyCharts();
  }

  private toNumber(value: any): number {
    const number = Number(value);

    return Number.isFinite(number) ? number : 0;
  }

  formatCurrency(value: number): string {
    return `₹${value.toLocaleString('en-IN', {
      maximumFractionDigits: 0,
    })}`;
  }

  /**
   * Deterministic color for a chart/list category at a given
   * position, drawn from the shared CATEGORY_PALETTE so a chart
   * segment and its matching row swatch are always the same color.
   */
  swatchColor(index: number): string {
    return CATEGORY_PALETTE[index % CATEGORY_PALETTE.length];
  }

  /**
   * Deterministic color for a named advisor, drawn from the same
   * shared palette. Hashing the name (rather than using list
   * position) means a given advisor is always the same color in the
   * Allocation by Advisor chart, the Advisor Performance chart, and
   * both of their row lists — even though the two lists are sorted
   * differently (by value vs. by return).
   */
  advisorColor(name: string): string {
    const value = (name || 'Unassigned').trim() || 'Unassigned';

    let hash = 0;

    for (let i = 0; i < value.length; i++) {
      hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
    }

    return CATEGORY_PALETTE[hash % CATEGORY_PALETTE.length];
  }

  /**
   * One- or two-letter initials for an advisor's avatar chip.
   */
  advisorInitials(name: string): string {
    const value = (name || 'Unassigned').trim() || 'Unassigned';

    const words = value.split(/\s+/).filter(Boolean);

    if (words.length === 1) {
      return words[0].slice(0, 2).toUpperCase();
    }

    return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  }

  formatAxisCurrency(value: number): string {
    const absolute = Math.abs(value);

    if (absolute >= 10000000) {
      return `₹${(value / 10000000).toFixed(1)}Cr`;
    }

    if (absolute >= 100000) {
      return `₹${(value / 100000).toFixed(1)}L`;
    }

    if (absolute >= 1000) {
      return `₹${(value / 1000).toFixed(0)}K`;
    }

    return `₹${value}`;
  }

  formatDate(value: string): string {
    if (!value) {
      return '';
    }

    const date = new Date(`${value}T00:00:00`);

    return date.toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
    });
  }

  formatCategory(value: string): string {
    if (!value) {
      return 'Unknown';
    }

    return value
      .replace(/_/g, ' ')
      .toLowerCase()
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }

  getBestPerformerName(): string {
    if (!this.bestPerformer) {
      return '-';
    }

    return (
      this.bestPerformer.asset_class ||
      this.bestPerformer.symbol ||
      this.bestPerformer.asset_name ||
      this.bestPerformer.scheme_name ||
      this.bestPerformer.name ||
      'Unknown'
    );
  }

  getWorstPerformerName(): string {
    if (!this.worstPerformer) {
      return '-';
    }

    return (
      this.worstPerformer.asset_class ||
      this.worstPerformer.symbol ||
      this.worstPerformer.asset_name ||
      this.worstPerformer.scheme_name ||
      this.worstPerformer.name ||
      'Unknown'
    );
  }

  getBestPerformerReturn(): number {
    return this.toNumber(this.bestPerformer?.pnl_percentage);
  }

  getWorstPerformerReturn(): number {
    return this.toNumber(this.worstPerformer?.pnl_percentage);
  }

  getLargestAllocationName(): string {
    if (!this.largestAllocation) {
      return '-';
    }

    return this.formatCategory(this.largestAllocation.category);
  }

  getLargestAllocationPercentage(): number {
    return this.toNumber(this.largestAllocation?.percentage);
  }
}
