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

import { WealthApiService } from '../../core/services/wealth-api.service';

import {
  PortfolioApiService,
  PortfolioTreeResponse,
} from '../../core/services/portfolio-api.service';

import {
  PortfolioReportPdfService,
  SubClassDetail,
  SubClassSummaryRow,
} from '../../core/services/portfolio-report-pdf.service';

Chart.register(...registerables);

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent implements OnInit, AfterViewInit, OnDestroy {
  private readonly wealthApi = inject(WealthApiService);
  private readonly portfolioApi = inject(PortfolioApiService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly reportPdf = new PortfolioReportPdfService();

  @ViewChild('wealthChart')
  wealthChartRef?: ElementRef<HTMLCanvasElement>;

  @ViewChild('allocationChart')
  allocationChartRef?: ElementRef<HTMLCanvasElement>;

  loading = true;
  error = '';

  summary: any = null;
  xirr: any = null;
  historical: any = null;
  investmentSummary: any = null;
  investmentSummaryError = '';

  /*
   * Allocation/performance by Advisor - fetched alongside the rest
   * of the Dashboard's data but not currently rendered on the page
   * itself; used only to populate the "Advisor Comparison" section
   * of the downloadable Portfolio Review PDF (see
   * downloadPortfolioReview()). Failure to load either is silent
   * (an empty array), matching the PDF service's own graceful
   * handling of an empty Advisor section, since this data isn't
   * critical to the Dashboard page loading successfully.
   */
  advisorAllocation: any[] = [];
  advisorPerformance: any[] = [];

  generatingReport = false;

  portfolioTree: PortfolioTreeResponse | null = null;

  private wealthChart?: Chart;
  private allocationChart?: Chart;

  private viewReady = false;

  /*
   * Currently expanded Asset Category in the
   * Investment Summary table.
   */
  expandedInvestmentCategory = '';

  /*
   * Currently selected Asset Category in the
   * XIRR Performance section.
   */
  xirrPerformanceAssetCategoryIndex = 0;

  /*
   * Currently selected Family Name filter.
   *
   * Empty string means "All Families". Changing this triggers a
   * full reload: Summary/XIRR/Investment Summary/Historical are all
   * re-fetched from the backend scoped to this Family (see
   * loadDashboard()); the XIRR Performance section is filtered
   * client-side from the already-loaded Portfolio Tree, the same way
   * Portfolio/Reports filter by Family.
   */
  selectedFamily = '';

  ngOnInit(): void {
    this.loadDashboard();
  }

  ngAfterViewInit(): void {
    this.viewReady = true;

    if (!this.loading) {
      setTimeout(() => {
        this.renderCharts();
        this.cdr.markForCheck();
      });
    }
  }

  /**
   * Distinct Family Names for the filter bar, alphabetically sorted.
   *
   * Sourced from the Portfolio Tree (always unfiltered), the same
   * way Portfolio/Reports build their Family filter options - so the
   * option list stays complete no matter which Family is currently
   * selected.
   */
  get familyOptions(): string[] {
    const names = new Set<string>();

    for (const family of this.portfolioTree?.families ?? []) {
      if (family.family_name) {
        names.add(family.family_name);
      }
    }

    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }

  isFamilySelected(family: string): boolean {
    return this.selectedFamily === family;
  }

  /**
   * Select a Family filter (or toggle it off if already selected)
   * and reload every family-aware section of the Dashboard.
   */
  selectFamily(family: string): void {
    this.selectedFamily = this.selectedFamily === family ? '' : family;

    this.xirrPerformanceAssetCategoryIndex = 0;

    this.loadDashboard();
  }

  clearFamily(): void {
    if (!this.selectedFamily) {
      return;
    }

    this.selectedFamily = '';

    this.xirrPerformanceAssetCategoryIndex = 0;

    this.loadDashboard();
  }

  loadDashboard(): void {
    console.log('Loading dashboard data...');

    this.loading = true;
    this.error = '';

    this.destroyCharts();

    const family = this.selectedFamily || undefined;

    // SUMMARY
    this.wealthApi.getSummary(family).subscribe({
      next: (data) => {
        console.log('SUMMARY RESPONSE:', data);

        this.summary = data;

        this.loading = false;

        this.cdr.markForCheck();

        setTimeout(() => {
          this.renderCharts();
          this.cdr.markForCheck();
        });
      },

      error: (error) => {
        console.error('SUMMARY API ERROR:', error);

        this.loading = false;
        this.error = 'Unable to load wealth summary.';

        this.cdr.markForCheck();
      },
    });

    // XIRR
    this.wealthApi.getXirr(family).subscribe({
      next: (data) => {
        console.log('XIRR RESPONSE:', data);

        this.xirr = data;

        this.cdr.markForCheck();
      },

      error: (error) => {
        console.error('XIRR API ERROR:', error);
      },
    });

    // INVESTMENT SUMMARY
    this.investmentSummary = null;
    this.investmentSummaryError = '';

    this.wealthApi.getInvestmentSummary(family).subscribe({
      next: (data) => {
        console.log('INVESTMENT SUMMARY RESPONSE:', data);

        this.investmentSummary = data;

        this.cdr.markForCheck();

        setTimeout(() => {
          this.renderAllocationChart();
          this.cdr.markForCheck();
        });
      },

      error: (error) => {
        console.error('INVESTMENT SUMMARY API ERROR:', error);

        this.investmentSummaryError = 'Unable to load investment summary.';

        this.cdr.markForCheck();
      },
    });

    // ADVISOR ALLOCATION / PERFORMANCE
    // (see the advisorAllocation/advisorPerformance field docs)
    this.wealthApi.getAllocationByAdvisor().subscribe({
      next: (data) => {
        this.advisorAllocation = data?.results ?? [];
        this.cdr.markForCheck();
      },

      error: (error) => {
        console.error('ALLOCATION BY ADVISOR API ERROR:', error);
        this.advisorAllocation = [];
      },
    });

    this.wealthApi.getPerformanceByAdvisor().subscribe({
      next: (data) => {
        this.advisorPerformance = data?.results ?? [];
        this.cdr.markForCheck();
      },

      error: (error) => {
        console.error('PERFORMANCE BY ADVISOR API ERROR:', error);
        this.advisorPerformance = [];
      },
    });

    // PORTFOLIO TREE
    /*
     * Reuse the existing Portfolio tree because every portfolio
     * asset already contains its calculated XIRR and Underlying.
     *
     * This is used for the Dashboard XIRR Performance section AND
     * as the source of Family Names for the filter bar - it is
     * intentionally NOT scoped by ?family= (the tree endpoint has no
     * such param), so the filter's own option list always shows
     * every Family regardless of which one is currently selected.
     * The XIRR Performance getters below filter it client-side by
     * selectedFamily, the same way Portfolio/Reports do.
     */
    this.portfolioApi.getPortfolioTree().subscribe({
      next: (data) => {
        console.log('PORTFOLIO TREE RESPONSE:', data);

        this.portfolioTree = data;

        this.ensureValidXirrCategoryIndex();

        this.cdr.markForCheck();
      },

      error: (error) => {
        console.error('PORTFOLIO TREE API ERROR:', error);

        this.portfolioTree = null;

        this.cdr.markForCheck();
      },
    });

    // HISTORICAL
    this.wealthApi.getHistorical(30, family).subscribe({
      next: (data) => {
        console.log('HISTORICAL RESPONSE:', data);

        this.historical = data;

        this.cdr.markForCheck();

        setTimeout(() => {
          this.renderWealthChart();
          this.cdr.markForCheck();
        });
      },

      error: (error) => {
        console.error('HISTORICAL API ERROR:', error);
      },
    });
  }

  private renderCharts(): void {
    if (this.loading || !this.viewReady) {
      return;
    }

    if (this.historical) {
      this.renderWealthChart();
    }

    if (this.investmentSummary) {
      this.renderAllocationChart();
    }
  }

  private renderWealthChart(): void {
    if (this.loading || !this.viewReady || !this.historical) {
      return;
    }

    const canvas = this.wealthChartRef?.nativeElement;

    if (!canvas) {
      return;
    }

    this.wealthChart?.destroy();

    const results = this.historical?.results ?? [];

    if (!results.length) {
      return;
    }

    const labels = results.map((item: any) => this.formatDate(item.date));

    const investedValues = results.map((item: any) => this.toNumber(item.invested_value));

    const portfolioValues = results.map((item: any) => this.toNumber(item.portfolio_value));

    const config: ChartConfiguration<'line'> = {
      type: 'line',

      data: {
        labels,

        datasets: [
          {
            label: 'Portfolio Value',
            data: portfolioValues,
            borderColor: '#111827',
            backgroundColor: 'rgba(17, 24, 39, 0.08)',
            borderWidth: 2,
            fill: true,
            tension: 0.35,
            pointRadius: 0,
            pointHoverRadius: 5,
          },

          {
            label: 'Invested Value',
            data: investedValues,
            borderColor: '#8b95a7',
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
              maxTicksLimit: 8,
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

    this.wealthChart = new Chart(canvas, config);
  }

  private renderAllocationChart(): void {
    if (this.loading || !this.viewReady || !this.investmentSummary) {
      return;
    }

    const canvas = this.allocationChartRef?.nativeElement;

    if (!canvas) {
      return;
    }

    this.allocationChart?.destroy();

    const results = this.allocationByCategory;

    if (!results.length) {
      return;
    }

    const labels = results.map((item) => item.category);

    const values = results.map((item) => item.value);

    const percentages = results.map((item) => item.percentage);

    const config: ChartConfiguration<'doughnut'> = {
      type: 'doughnut',

      data: {
        labels,

        datasets: [
          {
            data: values,

            backgroundColor: [
              '#111827',
              '#334155',
              '#64748b',
              '#94a3b8',
              '#cbd5e1',
              '#475569',
              '#1e293b',
              '#e2e8f0',
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

    this.allocationChart = new Chart(canvas, config);
  }

  private destroyCharts(): void {
    this.wealthChart?.destroy();
    this.allocationChart?.destroy();

    this.wealthChart = undefined;
    this.allocationChart = undefined;
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

  formatPercentage(value: number): string {
    return `${this.toNumber(value).toFixed(2)}%`;
  }

  /**
   * Groups the Investment Summary rows by Asset Category, summing
   * current value and % of total, so the Allocation chart shows the
   * exact same categorization and totals as the Investment Summary
   * table below it — one source of truth for both.
   *
   * CORRECTION: an earlier version of this getter assumed the
   * backend (InvestmentSummaryService.calculate(), reached via
   * /api/analytics/wealth/investment-summary/) returned a bare
   * array. That assumption was wrong — traced and confirmed against
   * the real service code and a live functional test — the backend
   * actually returns { results: [...], total_current_value }, and
   * each row genuinely carries percentage_of_total. The "fix" based
   * on the wrong assumption broke this section (empty Allocation/
   * Investment Summary); this restores the correct original logic.
   */
  get allocationByCategory(): Array<{
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

  /**
   * Groups the flat Investment Summary API rows by Asset Category.
   *
   * Level 1:
   *   Asset Category
   *
   * Level 2:
   *   Asset Class
   *
   * Clicking an Asset Category expands its Asset Classes directly
   * inside the Dashboard. No Portfolio navigation is performed.
   */
  get investmentSummaryGroups(): Array<{
    asset_category: string;
    current_value: number;
    percentage_of_total: number;
    asset_classes: Array<{
      asset_class: string;
      current_value: number;
      percentage_of_total: number;
      raw_asset_classes: string[];
    }>;
  }> {
    const results = this.investmentSummary?.results ?? [];

    const groups = new Map<
      string,
      {
        asset_category: string;
        current_value: number;
        percentage_of_total: number;
        asset_classes: Array<{
          asset_class: string;
          current_value: number;
          percentage_of_total: number;
          raw_asset_classes: string[];
        }>;
      }
    >();

    for (const row of results) {
      const category = row.asset_category || 'Unassigned';

      const assetClass = row.asset_class || 'Unassigned';

      let group = groups.get(category);

      if (!group) {
        group = {
          asset_category: category,
          current_value: 0,
          percentage_of_total: 0,
          asset_classes: [],
        };

        groups.set(category, group);
      }

      const currentValue = this.toNumber(row.current_value);

      const percentage = this.toNumber(row.percentage_of_total);

      group.current_value += currentValue;

      group.percentage_of_total += percentage;

      let classRow = group.asset_classes.find((item) => item.asset_class === assetClass);

      if (!classRow) {
        classRow = {
          asset_class: assetClass,
          current_value: 0,
          percentage_of_total: 0,
          raw_asset_classes: [],
        };

        group.asset_classes.push(classRow);
      }

      classRow.current_value += currentValue;

      classRow.percentage_of_total += percentage;

      const rawAssetClasses = Array.isArray(row.raw_asset_classes) ? row.raw_asset_classes : [];

      for (const rawAssetClass of rawAssetClasses) {
        if (rawAssetClass && !classRow.raw_asset_classes.includes(rawAssetClass)) {
          classRow.raw_asset_classes.push(rawAssetClass);
        }
      }
    }

    return Array.from(groups.values()).map((group) => ({
      ...group,

      percentage_of_total: Math.round(group.percentage_of_total * 100) / 100,

      asset_classes: group.asset_classes.map((assetClass) => ({
        ...assetClass,

        percentage_of_total: Math.round(assetClass.percentage_of_total * 100) / 100,
      })),
    }));
  }

  /**
   * Expand / collapse an Asset Category in Investment Summary.
   */
  toggleInvestmentCategory(category: string): void {
    if (this.expandedInvestmentCategory === category) {
      this.expandedInvestmentCategory = '';
      return;
    }

    this.expandedInvestmentCategory = category;
  }

  trackByInvestmentCategory(
    _index: number,
    group: {
      asset_category: string;
    },
  ): string {
    return group.asset_category;
  }

  trackByInvestmentAssetClass(
    _index: number,
    row: {
      asset_class: string;
    },
  ): string {
    return row.asset_class;
  }

  /* ============================================================
     XIRR PERFORMANCE
     ============================================================ */

  /**
   * Asset Categories available for the XIRR selector.
   *
   * The order comes from Investment Summary:
   *
   * Other
   * Alternate
   * Equities
   * Fixed Income
   * Liquids
   */
  get xirrPerformanceCategories(): string[] {
    const categories = this.investmentSummaryGroups.map((group) => group.asset_category);

    return categories.filter((category) => this.hasXirrDataForCategory(category));
  }

  /**
   * Currently selected Asset Category.
   */
  get selectedXirrAssetCategory(): string {
    const categories = this.xirrPerformanceCategories;

    if (!categories.length) {
      return '';
    }

    this.ensureValidXirrCategoryIndex();

    return categories[this.xirrPerformanceAssetCategoryIndex] ?? categories[0];
  }

  /**
   * Move to the previous Asset Category.
   */
  previousXirrAssetCategory(): void {
    const categories = this.xirrPerformanceCategories;

    if (!categories.length) {
      return;
    }

    this.ensureValidXirrCategoryIndex();

    this.xirrPerformanceAssetCategoryIndex =
      this.xirrPerformanceAssetCategoryIndex <= 0
        ? categories.length - 1
        : this.xirrPerformanceAssetCategoryIndex - 1;
  }

  /**
   * Move to the next Asset Category.
   */
  nextXirrAssetCategory(): void {
    const categories = this.xirrPerformanceCategories;

    if (!categories.length) {
      return;
    }

    this.ensureValidXirrCategoryIndex();

    this.xirrPerformanceAssetCategoryIndex =
      this.xirrPerformanceAssetCategoryIndex >= categories.length - 1
        ? 0
        : this.xirrPerformanceAssetCategoryIndex + 1;
  }

  /**
   * Return the XIRR rows for the currently selected
   * Asset Category.
   *
   * Each row represents the exact portfolio-tree asset
   * position, so the XIRR is the XIRR already calculated
   * by the existing PortfolioMetricsService.
   */
  get selectedXirrRows(): Array<{
    underlying: string;
    xirr: number;
    assetClass: string;
  }> {
    const category = this.selectedXirrAssetCategory;

    if (!category || !this.portfolioTree) {
      return [];
    }

    const rows: Array<{
      underlying: string;
      xirr: number;
      assetClass: string;
    }> = [];

    for (const family of this.portfolioTree.families ?? []) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) {
        continue;
      }

      for (const portfolio of family.portfolios ?? []) {
        for (const assetClass of portfolio.asset_classes ?? []) {
          for (const subClass of assetClass.sub_classes ?? []) {
            const assetCategory = this.getAssetCategoryForTreeAssetClass(subClass.sub_class);

            if (assetCategory !== category) {
              continue;
            }

            for (const asset of subClass.assets ?? []) {
              const xirr = Number(asset.xirr);

              if (!Number.isFinite(xirr)) {
                continue;
              }

              const underlying =
                asset.underlying?.trim() || asset.asset_name?.trim() || 'Unnamed Underlying';

              rows.push({
                underlying,
                xirr,
                assetClass: subClass.sub_class,
              });
            }
          }
        }
      }
    }

    return rows.sort((a, b) => b.xirr - a.xirr);
  }

  /**
   * Top 5 Underlyings by XIRR.
   */
  get topXirrRows(): Array<{
    underlying: string;
    xirr: number;
    assetClass: string;
  }> {
    return this.selectedXirrRows.slice(0, 5);
  }

  /**
   * Bottom 5 Underlyings by XIRR.
   */
  get bottomXirrRows(): Array<{
    underlying: string;
    xirr: number;
    assetClass: string;
  }> {
    return [...this.selectedXirrRows].sort((a, b) => a.xirr - b.xirr).slice(0, 5);
  }

  /**
   * Return true when a category contains at least one
   * valid XIRR record.
   */
  private hasXirrDataForCategory(category: string): boolean {
    if (!this.portfolioTree) {
      return false;
    }

    for (const family of this.portfolioTree.families ?? []) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) {
        continue;
      }

      for (const portfolio of family.portfolios ?? []) {
        for (const assetClass of portfolio.asset_classes ?? []) {
          for (const subClass of assetClass.sub_classes ?? []) {
            const assetCategory = this.getAssetCategoryForTreeAssetClass(subClass.sub_class);

            if (assetCategory !== category) {
              continue;
            }

            for (const asset of subClass.assets ?? []) {
              const xirr = Number(asset.xirr);

              if (Number.isFinite(xirr)) {
                return true;
              }
            }
          }
        }
      }
    }

    return false;
  }

  /**
   * Resolve the Asset Category for a Portfolio Tree Sub Class.
   *
   * IMPORTANT:
   *
   * This must be called with the Portfolio Tree's SUB CLASS value
   * (e.g. "Debt Mutual Fund", "Arbitrage Mutual Fund", "Direct
   * Equity"), NOT the broader top-level Asset Class value.
   *
   * The backend's Investment Summary categorization
   * (investment_summary.py) resolves its own "Asset Class" concept
   * as the SUB CLASS of each transaction, not the Portfolio Tree's
   * separate, broader asset_class field. Matching against the
   * wrong field meant categories like Fixed Income, Liquids, and
   * Other never matched anything and silently dropped out of the
   * XIRR Performance selector, even though their underlying assets
   * had valid XIRR data.
   *
   * Investment Summary already contains both:
   *
   *   canonical Asset Class
   *   raw Asset Class values
   *
   * so this avoids changing the backend Portfolio Tree.
   */
  private getAssetCategoryForTreeAssetClass(treeSubClass: string): string | null {
    const cleaned = (treeSubClass || '').trim();

    if (!cleaned) {
      return null;
    }

    for (const group of this.investmentSummaryGroups) {
      for (const assetClass of group.asset_classes) {
        if (assetClass.asset_class === cleaned) {
          return group.asset_category;
        }

        if (assetClass.raw_asset_classes.some((raw) => raw.trim() === cleaned)) {
          return group.asset_category;
        }
      }
    }

    /*
     * The Portfolio Tree can contain a raw Excel
     * classification that was normalized by the backend.
     *
     * These fallbacks mirror the existing Investment
     * Summary normalization rules.
     */
    const upper = cleaned.toUpperCase();

    if (upper.includes('EQUITY AIF') || upper === 'AIF') {
      return 'Equities';
    }

    if (upper.includes('EQUITY PMS') || upper === 'PMS') {
      return 'Equities';
    }

    if (upper.includes('EQUITY MUTUAL FUND')) {
      return 'Equities';
    }

    if (upper.includes('EQUITY LRS') || upper === 'LRS') {
      return 'Equities';
    }

    if (upper.includes('DIRECT EQUITY') || upper === 'EQUITY' || upper === 'STOCK') {
      return 'Equities';
    }

    if (upper.includes('DEBT MUTUAL FUND')) {
      return 'Fixed Income';
    }

    if (upper.includes('GOLD BOND') || upper === 'SGB' || upper.includes('SOVEREIGN GOLD')) {
      return 'Fixed Income';
    }

    if (upper.includes('ARBITRAGE')) {
      return 'Liquids';
    }

    if (upper.includes('LIQUID')) {
      return 'Liquids';
    }

    if (upper.includes('PRIVATE EQUITY')) {
      return 'Alternate';
    }

    if (upper.includes('REIT')) {
      return 'Alternate';
    }

    if (upper.includes('INVIT')) {
      return 'Alternate';
    }

    if (upper.includes('COMMODITY')) {
      return 'Alternate';
    }

    if (upper.includes('UNLISTED')) {
      return 'Other';
    }

    return null;
  }

  /**
   * Keep the selected category index valid after API
   * responses arrive or the available categories change.
   */
  private ensureValidXirrCategoryIndex(): void {
    const categories = this.xirrPerformanceCategories;

    if (!categories.length) {
      this.xirrPerformanceAssetCategoryIndex = 0;
      return;
    }

    if (this.xirrPerformanceAssetCategoryIndex >= categories.length) {
      this.xirrPerformanceAssetCategoryIndex = 0;
    }

    if (this.xirrPerformanceAssetCategoryIndex < 0) {
      this.xirrPerformanceAssetCategoryIndex = categories.length - 1;
    }
  }

  /**
   * Color class for an XIRR value based on its actual sign,
   * not on whether it appears in the Top or Bottom panel.
   */
  getXirrClass(value: number): string {
    if (value > 0) {
      return 'xirr-positive';
    }

    if (value < 0) {
      return 'xirr-negative';
    }

    return 'xirr-neutral';
  }

  formatXirr(value: number): string {
    return `${this.toNumber(value).toFixed(2)}%`;
  }

  trackByXirrUnderlying(
    index: number,
    row: {
      underlying: string;
      xirr: number;
      assetClass: string;
    },
  ): string {
    return `${row.underlying}::${row.assetClass}::${index}`;
  }

  private formatAxisCurrency(value: number): string {
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

  private formatDate(value: string): string {
    if (!value) {
      return '';
    }

    const date = new Date(`${value}T00:00:00`);

    return date.toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
    });
  }

  /**
   * Sub Class rollup for the Portfolio Review PDF's holdings page:
   * sums current/invested value and P&L per Sub Class across the
   * (already-loaded) Portfolio Tree, scoped to the currently
   * selected Family - the same source data and Family scoping the
   * live Dashboard/Portfolio pages already use.
   *
   * Deliberately does NOT compute a rolled-up XIRR here: Portfolio's
   * own subClassSummaries getter derives that via a real XIRR
   * calculation over each asset's cashflows, and duplicating an
   * approximation of that here risks silently disagreeing with the
   * number shown on the Portfolio page. Better to omit it in the
   * PDF than to show a number that might not match.
   */
  private buildSubClassSummariesForReport(): SubClassSummaryRow[] {
    const totals = new Map<
      string,
      { current_value: number; invested_value: number; pnl: number }
    >();

    for (const family of this.portfolioTree?.families ?? []) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) {
        continue;
      }

      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          for (const subClass of assetClass.sub_classes) {
            const key = subClass.sub_class || 'Unassigned';

            const existing = totals.get(key) ?? {
              current_value: 0,
              invested_value: 0,
              pnl: 0,
            };

            for (const asset of subClass.assets) {
              existing.current_value += asset.current_value ?? 0;
              existing.invested_value += asset.invested_value ?? 0;
              existing.pnl += asset.pnl ?? 0;
            }

            totals.set(key, existing);
          }
        }
      }
    }

    return Array.from(totals.entries())
      .map(([sub_class, values]) => ({
        sub_class,
        ...values,
      }))
      .sort((a, b) => b.current_value - a.current_value);
  }

  /**
   * Per-scheme/per-holding detail for the Portfolio Review PDF,
   * grouped by Sub Class - the source of the "Equities: Mutual
   * Funds/ETFs", "Fixed Income: Bonds" etc. style detail pages.
   * Every field here comes straight from the Portfolio Tree's own
   * PortfolioAssetNode (the same data the Portfolio page's
   * Underlying table already renders) - no new calculation.
   */
  private buildSubClassDetailsForReport(): SubClassDetail[] {
    const bySubClass = new Map<string, SubClassDetail>();

    for (const family of this.portfolioTree?.families ?? []) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) {
        continue;
      }

      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          for (const subClass of assetClass.sub_classes) {
            const key = subClass.sub_class || 'Unassigned';

            const existing = bySubClass.get(key) ?? {
              sub_class: key,
              assets: [],
            };

            for (const asset of subClass.assets) {
              existing.assets.push({
                asset_name: asset.asset_name || asset.underlying || '-',
                isin: asset.isin,
                advisors: asset.advisors,
                quantity: asset.quantity ?? 0,
                average_cost: asset.average_cost ?? 0,
                invested_value: asset.invested_value ?? 0,
                current_price: asset.current_price ?? 0,
                current_value: asset.current_value ?? 0,
                pnl: asset.pnl ?? 0,
                pnl_percentage: asset.pnl_percentage ?? 0,
                xirr: asset.xirr,
              });
            }

            bySubClass.set(key, existing);
          }
        }
      }
    }

    return Array.from(bySubClass.values()).sort((a, b) => a.sub_class.localeCompare(b.sub_class));
  }

  /**
   * Builds and downloads the "Portfolio Review" PDF from data
   * already loaded on this page - see
   * core/services/portfolio-report-pdf.service.ts for what it
   * contains and why some institutional-report sections (scheme
   * overlap, credit ratings, sector look-through) aren't included
   * yet: they need a market-data vendor feed PWMS doesn't currently
   * have.
   */
  downloadPortfolioReview(): void {
    this.generatingReport = true;

    try {
      this.reportPdf.generate({
        familyName: this.selectedFamily,
        totalWealth: this.summary?.total_current_value ?? this.summary?.total_wealth ?? 0,
        totalInvested: this.summary?.total_invested ?? this.summary?.invested_value ?? 0,
        totalPnl: this.summary?.total_pnl ?? this.summary?.pnl ?? 0,
        xirrPercentage: this.xirr?.xirr_percentage ?? this.summary?.xirr_percentage ?? null,
        investmentSummary: this.investmentSummary?.results ?? [],
        advisorAllocation: this.advisorAllocation,
        advisorPerformance: this.advisorPerformance,
        subClassSummaries: this.buildSubClassSummariesForReport(),
        subClassDetails: this.buildSubClassDetailsForReport(),
      });
    } catch (error) {
      console.error('Failed to generate Portfolio Review PDF:', error);
    } finally {
      this.generatingReport = false;
    }
  }
}
