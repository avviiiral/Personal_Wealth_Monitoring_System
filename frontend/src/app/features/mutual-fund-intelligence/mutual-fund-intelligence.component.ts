import { CommonModule } from '@angular/common';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Component, OnDestroy, OnInit, ChangeDetectorRef, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Chart, registerables } from 'chart.js';
import { Subscription } from 'rxjs';
import { environment } from '../../../environments/environment';

Chart.register(...registerables);

interface FundOption {
  scheme_code: string;
  scheme_name: string;
  amc_name: string;
  plan: string;
  option: string;
  level_1_branch: string;
  level_2_group: string;
  level_3_category: string;
}

interface CategoryFundsResponse {
  categories: string[];
  selected_category: string | null;
  fund_count: number;
  funds: FundOption[];
}

interface RankingRow {
  Scheme_Code: string;
  Scheme_Name: string;
  AMC_Name?: string;
  Level_1_Branch?: string;
  Level_2_Group?: string;
  Level_3_Category: string;
  Plan: string;
  CAGR_1Y_pct?: number | null;
  CAGR_3Y_pct?: number | null;
  CAGR_5Y_pct?: number | null;
  Rolling_3Y_CAGR_Mean_pct?: number | null;
  Annualized_Volatility_pct?: number | null;
  Sharpe_Ratio?: number | null;
  Sortino_Ratio?: number | null;
  Max_Drawdown_pct?: number | null;
  Percentile_Rank_3Y?: number | null;
  Quartile?: string | null;
  Category_Avg_CAGR_1Y_pct?: number | null;
  Category_Avg_CAGR_3Y_pct?: number | null;
  Category_Avg_CAGR_5Y_pct?: number | null;
  Category_Avg_Annualized_Volatility_pct?: number | null;
  Category_Avg_Sharpe_Ratio?: number | null;
  Category_Avg_Max_Drawdown_pct?: number | null;
  [key: string]: unknown;
}

interface NavPoint {
  date: string;
  nav: number;
}

interface PortfolioHolding {
  isin: string;
  stock_name: string;
  sector: string;
  weight_pct: number;
}

interface PortfolioDisclosure {
  portfolio_date: string;
  source_file: string;
  holdings: PortfolioHolding[];
}

interface FundHistoryResponse {
  available: boolean;
  scheme: FundOption;
  history: NavPoint[];
  history_count: number;
  metrics: Record<string, number | string | null>;
  portfolio?: PortfolioDisclosure | null;
  message?: string | null;
}

interface AnalyticsSummary {
  available: boolean;
  generated_at?: string;
  source?: string;
  schemes_requested?: number;
  funds_with_history?: number;
  funds_without_history?: number;
  history_coverage?: { schemes_with_history?: number; schemes_requested?: number; coverage_pct?: number };
  tree_summary?: Array<Record<string, unknown>>;
  message?: string;
}

type DashboardTab = 'overview' | 'fund' | 'rankings' | 'performance' | 'calendar' | 'portfolio' | 'positioning' | 'universe' | 'attribution';

@Component({
  selector: 'app-mutual-fund-intelligence',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mutual-fund-intelligence.component.html',
  styleUrl: './mutual-fund-intelligence.component.scss',
})
export class MutualFundIntelligenceComponent implements OnInit, OnDestroy {
  private readonly http = inject(HttpClient);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly endpoint = `${environment.apiUrl}/api/mutual-fund-analytics/sebi`;

  categories: string[] = [];
  allFunds: FundOption[] = [];
  funds: FundOption[] = [];
  rankings: RankingRow[] = [];
  categorySummary: Record<string, unknown> | null = null;
  summary: AnalyticsSummary | null = null;
  history: NavPoint[] = [];
  metrics: Record<string, number | string | null> = {};
  portfolio: PortfolioDisclosure | null = null;
  selectedCategory = '';
  selectedPlan = 'Direct Plan';
  selectedFundCode = '';
  selectedFund: FundOption | null = null;
  activeTab: DashboardTab = 'overview';
  loadingCategories = false;
  loadingRankings = false;
  loadingHistory = false;
  errorMessage = '';
  historyMessage = '';
  readonly tabs: { id: DashboardTab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'fund', label: 'Fund Scorecard' },
    { id: 'rankings', label: 'Peer Rankings' },
    { id: 'performance', label: 'Performance' },
    { id: 'calendar', label: 'Calendar Year' },
    { id: 'portfolio', label: 'Portfolio Composition' },
    { id: 'positioning', label: 'Category Positioning' },
    { id: 'universe', label: 'Universe Table' },
    { id: 'attribution', label: 'Attribution' },
  ];

  private rankingsSubscription?: Subscription;
  private historySubscription?: Subscription;
  private categorySubscription?: Subscription;
  private charts: Chart[] = [];

  ngOnInit(): void {
    this.loadSummary();
    this.loadCategories();
  }

  ngOnDestroy(): void {
    this.destroyCharts();
    this.rankingsSubscription?.unsubscribe();
    this.historySubscription?.unsubscribe();
    this.categorySubscription?.unsubscribe();
  }

  get selectedCategoryFunds(): number {
    return this.funds.length;
  }

  get selectedCategoryRankings(): RankingRow[] {
    return this.rankings.filter(row => row.Plan === this.selectedPlan);
  }

  get categoryAverage(): Record<string, unknown> | null {
    return this.categorySummary;
  }

  get latestNav(): number | null {
    const value = this.history[this.history.length - 1]?.nav;
    return Number.isFinite(value) ? value : null;
  }

  get navChangePct(): number | null {
    if (this.history.length < 2) return null;
    const first = this.history[0].nav;
    return first > 0 ? ((this.history[this.history.length - 1].nav / first) - 1) * 100 : null;
  }

  loadSummary(): void {
    this.http.get<AnalyticsSummary>(`${this.endpoint}/summary/`, { withCredentials: true }).subscribe({
      next: response => {
        this.summary = response;
        this.cdr.detectChanges();
      },
      error: error => console.error('Unable to load SEBI analytics summary:', error),
    });
  }

  loadCategories(): void {
    this.loadingCategories = true;
    this.errorMessage = '';
    this.http.get<CategoryFundsResponse>(`${this.endpoint}/categories/`, { withCredentials: true }).subscribe({
      next: response => {
        this.categories = response.categories ?? [];
        this.loadingCategories = false;
        this.cdr.detectChanges();
      },
      error: error => {
        console.error('Unable to load SEBI categories:', error);
        this.errorMessage = error?.status === 401
          ? 'Your session has expired. Please sign in again.'
          : 'Unable to load categories. Please check the backend and try again.';
        this.loadingCategories = false;
        this.cdr.detectChanges();
      },
    });
  }

  onCategoryChange(category: string): void {
    this.selectedCategory = category;
    this.selectedFundCode = '';
    this.selectedFund = null;
    this.allFunds = [];
    this.funds = [];
    this.rankings = [];
    this.categorySummary = null;
    this.history = [];
    this.metrics = {};
    this.portfolio = null;
    this.historyMessage = '';
    this.errorMessage = '';
    this.historySubscription?.unsubscribe();
    this.categorySubscription?.unsubscribe();
    this.rankingsSubscription?.unsubscribe();
    this.categorySubscription?.unsubscribe();
    this.rankingsSubscription?.unsubscribe();

    if (!category) {
      this.loadingRankings = false;
      this.refreshCharts();
      return;
    }

    this.loadingRankings = true;
    const params = new HttpParams().set('category', category);
    this.categorySubscription = this.http.get<CategoryFundsResponse>(`${this.endpoint}/categories/`, {
      params, withCredentials: true,
    }).subscribe({
      next: response => {
        this.allFunds = response.funds ?? [];
        this.applyPlanFilter();
        this.loadRankings();
      },
      error: error => {
        console.error('Unable to load funds for category:', error);
        this.errorMessage = 'Unable to load funds for this category. Please try again.';
        this.loadingRankings = false;
        this.cdr.detectChanges();
      },
    });
  }

  onPlanChange(plan: string): void {
    this.selectedPlan = plan;
    this.selectedFundCode = '';
    this.selectedFund = null;
    this.history = [];
    this.metrics = {};
    this.portfolio = null;
    this.historyMessage = '';
    this.historySubscription?.unsubscribe();
    this.applyPlanFilter();
    if (this.selectedCategory) this.loadRankings();
    this.refreshCharts();
  }

  private applyPlanFilter(): void {
    this.funds = this.allFunds.filter(fund => fund.plan === this.selectedPlan);
    this.categorySummary = this.summary?.tree_summary?.find(row =>
      row['Level_3_Category'] === this.selectedCategory && row['Plan'] === this.selectedPlan,
    ) ?? null;
    this.cdr.detectChanges();
  }

  private loadRankings(): void {
    this.rankingsSubscription?.unsubscribe();
    this.loadingRankings = true;
    const params = new HttpParams()
      .set('category', this.selectedCategory)
      .set('plan', this.selectedPlan)
      .set('page', '1')
      .set('page_size', '500');
    this.rankingsSubscription = this.http.get<{ available: boolean; results: RankingRow[]; message?: string }>(
      `${this.endpoint}/rankings/`, { params, withCredentials: true },
    ).subscribe({
      next: response => {
        this.rankings = response.results ?? [];
        this.categorySummary = this.summary?.tree_summary?.find(row =>
          row['Level_3_Category'] === this.selectedCategory && row['Plan'] === this.selectedPlan,
        ) ?? null;
        this.loadingRankings = false;
        if (!response.available && response.message) this.errorMessage = response.message;
        this.cdr.detectChanges();
        this.refreshCharts();
      },
      error: error => {
        console.error('Unable to load SEBI rankings:', error);
        this.errorMessage = 'Unable to load the local rankings snapshot. Run the SEBI analytics build and retry.';
        this.loadingRankings = false;
        this.cdr.detectChanges();
      },
    });
  }

  onFundChange(schemeCode: string): void {
    this.selectedFundCode = schemeCode;
    this.selectedFund = this.funds.find(fund => fund.scheme_code === schemeCode) ?? null;
    this.history = [];
    this.metrics = {};
    this.portfolio = null;
    this.historyMessage = '';
    this.historySubscription?.unsubscribe();

    if (!this.selectedFund) {
      this.refreshCharts();
      return;
    }

    this.loadingHistory = true;
    this.historySubscription = this.http.get<FundHistoryResponse>(
      `${this.endpoint}/funds/${encodeURIComponent(schemeCode)}/history/`,
      { withCredentials: true },
    ).subscribe({
      next: response => {
        this.history = response.history ?? [];
        this.metrics = response.metrics ?? {};
        this.portfolio = response.portfolio ?? null;
        this.historyMessage = response.message ?? '';
        this.loadingHistory = false;
        this.cdr.detectChanges();
        this.refreshCharts();
      },
      error: error => {
        console.error('Unable to load selected fund NAV history:', error);
        this.historyMessage = error?.status === 404
          ? 'This scheme is no longer part of the active SEBI Growth universe.'
          : 'Unable to load locally stored NAV history. Please retry.';
        this.loadingHistory = false;
        this.cdr.detectChanges();
      },
    });
  }

  selectTab(tab: DashboardTab): void {
    this.activeTab = tab;
    this.cdr.detectChanges();
    this.refreshCharts();
  }

  clearSelection(): void {
    this.selectedCategory = '';
    this.selectedFundCode = '';
    this.selectedFund = null;
    this.allFunds = [];
    this.funds = [];
    this.rankings = [];
    this.categorySummary = null;
    this.history = [];
    this.metrics = {};
    this.historyMessage = '';
    this.errorMessage = '';
    this.historySubscription?.unsubscribe();
    this.refreshCharts();
  }

  retry(): void {
    if (this.selectedFundCode) this.onFundChange(this.selectedFundCode);
    else if (this.selectedCategory) {
      this.onCategoryChange(this.selectedCategory);
    } else this.loadCategories();
  }

  getMetric(row: RankingRow | null | undefined, key: keyof RankingRow): number | null {
    if (!row) return null;
    const value = row[key];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  getSelectedRanking(): RankingRow | null {
    return this.rankings.find(row => row.Scheme_Code === this.selectedFundCode) ?? null;
  }

  getMetricValue(key: string): number | null {
    const value = this.metrics[key];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  getCategoryMetric(key: string): number | null {
    const value = this.categorySummary?.[`Category_Avg_${key}`];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  getCalendarReturns(): Array<{ year: number; value: number | null }> {
    const result: Array<{ year: number; value: number | null }> = [];
    const years = [2021, 2022, 2023, 2024, 2025, new Date().getFullYear()];
    for (const year of years) {
      const start = this.navOnOrBefore(`${year}-01-01`);
      const end = this.navOnOrBefore(`${year}-12-31`);
      if (!start || !end || end.date.slice(0, 4) !== String(year) || start.nav <= 0) {
        result.push({ year, value: null });
      } else {
        result.push({ year, value: ((end.nav / start.nav) - 1) * 100 });
      }
    }
    return result;
  }

  private navOnOrBefore(target: string): NavPoint | null {
    let result: NavPoint | null = null;
    for (const point of this.history) {
      if (point.date <= target) result = point;
      else break;
    }
    return result;
  }

  getSectorAllocation(): Array<{ sector: string; weight: number }> {
    const totals = new Map<string, number>();
    for (const holding of this.portfolio?.holdings ?? []) {
      const sector = holding.sector?.trim() || 'Unclassified';
      totals.set(sector, (totals.get(sector) ?? 0) + Number(holding.weight_pct || 0));
    }
    return [...totals.entries()].map(([sector, weight]) => ({ sector, weight }))
      .sort((a, b) => b.weight - a.weight);
  }

  getTopFunds(metric: keyof RankingRow, limit = 10): RankingRow[] {
    return [...this.selectedCategoryRankings]
      .filter(row => this.getMetric(row, metric) !== null)
      .sort((a, b) => (this.getMetric(b, metric) ?? -Infinity) - (this.getMetric(a, metric) ?? -Infinity))
      .slice(0, limit);
  }

  getQuartileClass(value: string | null | undefined): string {
    return value ? value.toLowerCase() : 'na';
  }

  formatPct(value: number | null | undefined, digits = 2): string {
    return value === null || value === undefined || !Number.isFinite(value) ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;
  }

  formatNumber(value: number | null | undefined, digits = 2): string {
    return value === null || value === undefined || !Number.isFinite(value) ? '—' : value.toFixed(digits);
  }

  formatDate(value: string | undefined): string {
    if (!value) return '—';
    const date = new Date(`${value}T00:00:00`);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  formatGeneratedAt(value: string | undefined): string {
    if (!value) return 'Not generated';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('en-IN');
  }

  getCategorySummaryValue(key: string): string {
    const value = this.categorySummary?.[key];
    return typeof value === 'number' ? this.formatNumber(value) : '—';
  }

  private refreshCharts(): void {
    this.destroyCharts();
    setTimeout(() => {
      if (this.activeTab === 'overview') this.renderOverviewCharts();
      if (this.activeTab === 'fund') this.renderNavChart('fund-nav-chart');
      if (this.activeTab === 'performance') this.renderPerformanceChart();
      if (this.activeTab === 'calendar') this.renderCalendarChart();
      if (this.activeTab === 'portfolio') this.renderPortfolioChart();
      if (this.activeTab === 'positioning') this.renderPositioningChart();
    }, 0);
  }

  private destroyCharts(): void {
    this.charts.forEach(chart => chart.destroy());
    this.charts = [];
  }

  private chartOptions(): any {
    const dark = document.documentElement.classList.contains('dark-theme');
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: dark ? '#d7deea' : '#475467', usePointStyle: true, boxWidth: 8 } },
        tooltip: { intersect: false, mode: 'index' as const },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: dark ? '#9aa5b5' : '#667085', maxRotation: 35 } },
        y: { grid: { color: dark ? '#303846' : '#e8edf2' }, ticks: { color: dark ? '#9aa5b5' : '#667085' } },
      },
    };
  }

  private renderOverviewCharts(): void {
    const topCanvas = document.getElementById('overview-return-chart') as HTMLCanvasElement | null;
    if (topCanvas && this.selectedCategoryRankings.length) {
      const rows = this.getTopFunds('CAGR_3Y_pct', 12).reverse();
      const chart = new Chart(topCanvas, {
        type: 'bar',
        data: {
          labels: rows.map(row => row.Scheme_Name.replace(/ - (Direct|Regular) Plan - Growth$/i, '')),
          datasets: [{ label: '3Y CAGR', data: rows.map(row => this.getMetric(row, 'CAGR_3Y_pct')), backgroundColor: '#FD7740', borderRadius: 3 }],
        },
        options: { ...this.chartOptions(), indexAxis: 'y', plugins: { ...this.chartOptions().plugins, legend: { display: false } } },
      });
      this.charts.push(chart);
    }

    const riskCanvas = document.getElementById('overview-risk-chart') as HTMLCanvasElement | null;
    if (riskCanvas) this.renderScatter(riskCanvas);
  }

  private renderScatter(canvas: HTMLCanvasElement): void {
    const points = this.selectedCategoryRankings.filter(row =>
      this.getMetric(row, 'Annualized_Volatility_pct') !== null && this.getMetric(row, 'CAGR_3Y_pct') !== null,
    );
    if (!points.length) return;
    const chart = new Chart(canvas, {
      type: 'scatter',
      data: {
        datasets: [{
          label: 'Funds',
          data: points.map(row => ({
            x: this.getMetric(row, 'Annualized_Volatility_pct') as number,
            y: this.getMetric(row, 'CAGR_3Y_pct') as number,
          })),
          backgroundColor: points.map(row => row.Scheme_Code === this.selectedFundCode ? '#FD7740' : '#0B1C3D'),
          pointRadius: points.map(row => row.Scheme_Code === this.selectedFundCode ? 8 : 5),
        }],
      },
      options: {
        ...this.chartOptions(),
        plugins: {
          ...this.chartOptions().plugins,
          tooltip: { callbacks: { label: (context: any) => {
            const row = points[context.dataIndex];
            return `${row.Scheme_Name}: ${context.parsed.y.toFixed(2)}% return / ${context.parsed.x.toFixed(2)}% volatility`;
          } } },
        },
        scales: {
          x: { ...this.chartOptions().scales.x, title: { display: true, text: 'Annualized volatility (%)' } },
          y: { ...this.chartOptions().scales.y, title: { display: true, text: '3Y CAGR (%)' } },
        },
      },
    });
    this.charts.push(chart);
  }

  private renderNavChart(id: string): void {
    const canvas = document.getElementById(id) as HTMLCanvasElement | null;
    if (!canvas || !this.history.length) return;
    const chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels: this.history.map(point => point.date),
        datasets: [{
          label: 'NAV',
          data: this.history.map(point => point.nav),
          borderColor: '#FD7740',
          backgroundColor: 'rgba(253,119,64,.10)',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          fill: true,
          tension: .15,
        }],
      },
      options: {
        ...this.chartOptions(),
        interaction: { mode: 'index', intersect: false },
        scales: {
          ...this.chartOptions().scales,
          x: { ...this.chartOptions().scales.x, ticks: { ...this.chartOptions().scales.x.ticks, maxTicksLimit: 10 } },
          y: { ...this.chartOptions().scales.y, title: { display: true, text: 'NAV (₹)' } },
        },
      },
    });
    this.charts.push(chart);
  }

  private renderPerformanceChart(): void {
    const canvas = document.getElementById('performance-chart') as HTMLCanvasElement | null;
    if (!canvas) return;
    const rows = this.selectedCategoryRankings.filter(row => this.getMetric(row, 'CAGR_1Y_pct') !== null);
    if (!rows.length) return;
    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: rows.map(row => row.Scheme_Name.replace(/ - (Direct|Regular) Plan - Growth$/i, '')),
        datasets: [
          { label: '1Y CAGR', data: rows.map(row => this.getMetric(row, 'CAGR_1Y_pct')), backgroundColor: '#0B1C3D' },
          { label: '3Y CAGR', data: rows.map(row => this.getMetric(row, 'CAGR_3Y_pct')), backgroundColor: '#FD7740' },
          { label: '5Y CAGR', data: rows.map(row => this.getMetric(row, 'CAGR_5Y_pct')), backgroundColor: '#CC9F53' },
        ],
      },
      options: { ...this.chartOptions(), indexAxis: 'y', scales: { ...this.chartOptions().scales, x: { ...this.chartOptions().scales.x, title: { display: true, text: 'Annualised return (%)' } } } },
    });
    this.charts.push(chart);
  }

  private renderCalendarChart(): void {
    const canvas = document.getElementById('calendar-chart') as HTMLCanvasElement | null;
    if (!canvas || !this.history.length) return;
    const returns = this.getCalendarReturns();
    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: returns.map(row => String(row.year)),
        datasets: [{ label: 'Calendar-year NAV return', data: returns.map(row => row.value), backgroundColor: returns.map(row => (row.value ?? 0) >= 0 ? '#16A36A' : '#DC4C4C') }],
      },
      options: this.chartOptions(),
    });
    this.charts.push(chart);
  }

  private renderPortfolioChart(): void {
    const canvas = document.getElementById('portfolio-chart') as HTMLCanvasElement | null;
    const sectors = this.getSectorAllocation();
    if (!canvas || !sectors.length) return;
    const chart = new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: sectors.map(row => row.sector),
        datasets: [{ data: sectors.map(row => row.weight), backgroundColor: ['#FD7740', '#071735', '#CC9F53', '#3B82F6', '#16A36A', '#7C3AED', '#0891B2', '#D97706', '#DC2626'], borderWidth: 2, borderColor: document.documentElement.classList.contains('dark-theme') ? '#171c25' : '#ffffff' }],
      },
      options: { ...this.chartOptions(), cutout: '62%', plugins: { ...this.chartOptions().plugins, legend: { position: 'bottom' as const } } },
    });
    this.charts.push(chart);
  }

  private renderPositioningChart(): void {
    const canvas = document.getElementById('positioning-chart') as HTMLCanvasElement | null;
    if (canvas) this.renderScatter(canvas);
  }
}
