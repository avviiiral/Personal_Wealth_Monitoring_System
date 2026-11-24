import { CommonModule } from '@angular/common';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Component, OnDestroy, OnInit, ChangeDetectorRef, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Chart, registerables } from 'chart.js';
import { Subscription } from 'rxjs';
import { environment } from '../../environments/environment';
import { MutualFundDetailResponse, MutualFundIntelligenceApiService, MutualFundIntelligenceResponse } from '../../core/services/mutual-fund-intelligence-api.service';

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

interface CategoryReturnBin { label: string; start: number; '3M': number; '6M': number; '1Y': number; }
interface CategoryReturnDistribution {
  available: boolean; category: string; plan: string; bins: CategoryReturnBin[];
  funds_counted: Record<string, number>; as_of_date?: string; message?: string;
}

interface CategoryAAUMRow { scheme_code: string; scheme_name: string; amc_name: string; plan: string; average_aum_cr: number; }
interface CategoryAAUMResponse { available: boolean; category: string; plan: string; quarter_label?: string; rows: CategoryAAUMRow[]; message?: string; }

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

type DashboardTab = 'overview' | 'fund' | 'rankings' | 'performance' | 'calendar' | 'portfolio' | 'positioning' | 'universe' | 'attribution' | 'charliealpha';

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
  private readonly charliealphaApi = inject(MutualFundIntelligenceApiService);

  categories: string[] = [];
  allFunds: FundOption[] = [];
  funds: FundOption[] = [];
  rankings: RankingRow[] = [];
  categorySummary: Record<string, unknown> | null = null;
  summary: AnalyticsSummary | null = null;
  history: NavPoint[] = [];
  metrics: Record<string, number | string | null> = {};
  portfolio: PortfolioDisclosure | null = null;
  returnDistribution: CategoryReturnDistribution | null = null;
  categoryAAUM: CategoryAAUMResponse | null = null;
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
    { id: 'charliealpha', label: 'CharlieAlpha Intelligence' },
  ];

  private rankingsSubscription?: Subscription;
  private historySubscription?: Subscription;
  private categorySubscription?: Subscription;
  private distributionSubscription?: Subscription;
  private aaumSubscription?: Subscription;
  private charts: Chart[] = [];
  private charliealphaOverviewSubscription?: Subscription;
  private charliealphaFundSubscription?: Subscription;
  private charliealphaCompareSubscription?: Subscription;
  charliealphaLoading = false;
  charliealphaDetailLoading = false;
  charliealphaCompareLoading = false;
  charliealphaError = '';
  charliealphaDetailError = '';
  charliealphaCompareError = '';
  charliealphaOverview: MutualFundIntelligenceResponse['results'] | null = null;
  charliealphaFunds: Array<Record<string, any>> = [];
  charliealphaAmcs: string[] = [];
  charliealphaCategories: string[] = [];
  charliealphaPlans: string[] = [];
  charliealphaOptions: string[] = [];
  charliealphaSearch = '';
  charliealphaAmc = '';
  charliealphaCategory = '';
  charliealphaPlan = '';
  charliealphaOption = '';
  charliealphaSelectedCode = '';
  charliealphaDetail: MutualFundDetailResponse | null = null;
  charliealphaCompareCodes: string[] = [];
  charliealphaCompareResult: any = null;
  charliealphaBenchmarks: any[] = [];
  charliealphaBenchmarkSlug = '';
  charliealphaBenchmark: any = null;
  charliealphaFromDate = '';
  charliealphaToDate = '';
  charliealphaPage = 1;
  charliealphaTotalCount = 0;
  readonly charliealphaPageSize = 100;
  charliealphaNext: string | null = null;
  charliealphaPrevious: string | null = null;
  charliealphaSortField = 'scheme_name';
  charliealphaSortDirection: 'asc' | 'desc' = 'asc';
  charliealphaHoldingDate = '';
  charliealphaExposureIdentifier = '';
  charliealphaExposureResult: any = null;
  charliealphaExposureLoading = false;
  charliealphaExposureError = '';

  ngOnInit(): void {
    this.loadSummary();
    this.loadCategories();
  }

  ngOnDestroy(): void {
    this.destroyCharts();
    this.rankingsSubscription?.unsubscribe();
    this.historySubscription?.unsubscribe();
    this.categorySubscription?.unsubscribe();
    this.distributionSubscription?.unsubscribe();
    this.aaumSubscription?.unsubscribe();
    this.charliealphaOverviewSubscription?.unsubscribe();
    this.charliealphaFundSubscription?.unsubscribe();
    this.charliealphaCompareSubscription?.unsubscribe();
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
        if (this.selectedCategory) {
          this.categorySummary = this.summary?.tree_summary?.find(row =>
            row['Level_3_Category'] === this.selectedCategory && row['Plan'] === this.selectedPlan,
          ) ?? null;
        }
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
    this.distributionSubscription?.unsubscribe();
    this.aaumSubscription?.unsubscribe();
    this.returnDistribution = null;
    this.categoryAAUM = null;
    this.loadingHistory = false;

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
        this.loadReturnDistribution();
        this.loadCategoryAAUM();
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
    this.loadingHistory = false;
    this.applyPlanFilter();
    if (this.selectedCategory) { this.loadRankings(); this.loadReturnDistribution(); this.loadCategoryAAUM(); }
    this.refreshCharts();
  }

  private loadReturnDistribution(): void {
    this.distributionSubscription?.unsubscribe();
    this.returnDistribution = null;
    if (!this.selectedCategory) return;
    const params = new HttpParams().set('category', this.selectedCategory).set('plan', this.selectedPlan);
    this.distributionSubscription = this.http.get<CategoryReturnDistribution>(
      `${this.endpoint}/category-return-distribution/`, { params, withCredentials: true },
    ).subscribe({
      next: response => { this.returnDistribution = response; this.cdr.detectChanges(); this.refreshCharts(); },
      error: error => { console.error('Unable to load category return distribution:', error); this.returnDistribution = null; this.cdr.detectChanges(); },
    });
  }

  private loadCategoryAAUM(): void {
    this.aaumSubscription?.unsubscribe();
    this.categoryAAUM = null;
    if (!this.selectedCategory) return;
    const params = new HttpParams().set('category', this.selectedCategory).set('plan', this.selectedPlan);
    this.aaumSubscription = this.http.get<CategoryAAUMResponse>(
      `${this.endpoint}/category-top-aaum/`, { params, withCredentials: true },
    ).subscribe({
      next: response => { this.categoryAAUM = response; this.cdr.detectChanges(); this.refreshCharts(); },
      error: error => { console.error('Unable to load category AAUM:', error); this.categoryAAUM = null; this.cdr.detectChanges(); },
    });
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
      this.loadingHistory = false;
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
    if (tab === 'charliealpha') {
      this.loadCharlieAlphaOverview();
      this.loadCharlieAlphaBenchmarks();
    }
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
    this.portfolio = null;
    this.historyMessage = '';
    this.errorMessage = '';
    this.historySubscription?.unsubscribe();
    this.categorySubscription?.unsubscribe();
    this.rankingsSubscription?.unsubscribe();
    this.distributionSubscription?.unsubscribe();
    this.returnDistribution = null;
    this.loadingHistory = false;
    this.loadingRankings = false;
    this.refreshCharts();
  }

  retry(): void {
    if (this.selectedFundCode) this.onFundChange(this.selectedFundCode);
    else if (this.selectedCategory) {
      this.onCategoryChange(this.selectedCategory);
    } else this.loadCategories();
  }

  getDistributionCount(period: '3M' | '6M' | '1Y'): number {
    return this.returnDistribution?.funds_counted?.[period] ?? 0;
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

  formatGeneratedAt(value: string | null | undefined): string {
    if (!value) return 'Not generated';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('en-IN');
  }

  sourceLabel(value: string): string {
    return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
  }

  getCategorySummaryValue(key: string): string {
    const value = this.categorySummary?.[key];
    return typeof value === 'number' ? this.formatNumber(value) : '—';
  }

  private refreshCharts(): void {
    this.destroyCharts();
    setTimeout(() => {
      if (this.activeTab === 'overview') { this.renderOverviewCharts(); this.renderReturnDistributionChart(); this.renderAAUMChart(); }
      if (this.activeTab === 'fund') this.renderNavChart('fund-nav-chart');
      if (this.activeTab === 'performance') this.renderPerformanceChart();
      if (this.activeTab === 'calendar') this.renderCalendarChart();
      if (this.activeTab === 'portfolio') this.renderPortfolioChart();
      if (this.activeTab === 'positioning') this.renderPositioningChart();
      if (this.activeTab === 'charliealpha') this.renderCharlieAlphaCharts();
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

  private renderReturnDistributionChart(): void {
    const canvas = document.getElementById('category-return-distribution-chart') as HTMLCanvasElement | null;
    const bins = this.returnDistribution?.bins ?? [];
    if (!canvas || !bins.length) return;
    const base = this.chartOptions();
    const chart = new Chart(canvas, {
      type: 'bar',
      data: { labels: bins.map(bin => bin.label), datasets: [
        { label: '3M Return', data: bins.map(bin => bin['3M']), backgroundColor: 'rgba(253,119,64,.62)', borderColor: '#FD7740', borderWidth: 1 },
        { label: '6M Return', data: bins.map(bin => bin['6M']), backgroundColor: 'rgba(11,28,61,.68)', borderColor: '#0B1C3D', borderWidth: 1 },
        { label: '1Y Return', data: bins.map(bin => bin['1Y']), backgroundColor: 'rgba(204,159,83,.58)', borderColor: '#CC9F53', borderWidth: 1 },
      ] },
      options: { ...base, interaction: { mode: 'index', intersect: false },
        scales: {
          ...base.scales,
          x: { ...base.scales.x, title: { display: true, text: 'Return range (%)' }, ticks: { ...base.scales.x.ticks, maxRotation: 0, autoSkip: true, maxTicksLimit: 16 } },
          y: { ...base.scales.y, beginAtZero: true, title: { display: true, text: 'Number of funds' }, ticks: { ...base.scales.y.ticks, precision: 0, stepSize: 1 } },
        },
      },
    });
    this.charts.push(chart);
  }

  private renderAAUMChart(): void {
    const canvas = document.getElementById('category-aaum-chart') as HTMLCanvasElement | null;
    const rows = this.categoryAAUM?.rows ?? [];
    if (!canvas || !rows.length) return;
    const dark = document.documentElement.classList.contains('dark-theme');
    const ordered = [...rows].reverse();
    const base = this.chartOptions();
    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: ordered.map(row => row["scheme_name"].replace(/\s*-\s*(Direct|Regular) Plan\s*-\s*Growth$/i, '')),
        datasets: [{ label: 'Quarterly Average AUM (₹ Cr)', data: ordered.map(row => row.average_aum_cr), backgroundColor: '#0B1C3D', borderColor: '#FD7740', borderWidth: 1, borderRadius: 3 }],
      },
      options: {
        ...base, indexAxis: 'y', plugins: { ...base.plugins, legend: { display: false } },
        scales: {
          x: { grid: { color: dark ? '#303846' : '#e8edf2' }, ticks: { color: dark ? '#9aa5b5' : '#667085', callback: (value: string | number) => Number(value).toLocaleString('en-IN') }, title: { display: true, text: 'AUM (₹ Crore)' } },
          y: { grid: { display: false }, ticks: { color: dark ? '#9aa5b5' : '#667085', autoSkip: false } },
        },
      },
    });
    this.charts.push(chart);
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

  loadCharlieAlphaOverview(): void {
    this.charliealphaLoading = true;
    this.charliealphaError = '';
    const filters = {
      q: this.charliealphaSearch.trim(),
      amc: this.charliealphaAmc,
      category: this.charliealphaCategory,
      plan: this.charliealphaPlan,
      option: this.charliealphaOption,
      page: this.charliealphaPage,
      page_size: this.charliealphaPageSize,
    };
    this.charliealphaOverviewSubscription?.unsubscribe();
    this.charliealphaOverviewSubscription = this.charliealphaApi.getOverview(filters).subscribe({
      next: response => {
        this.charliealphaOverview = response.results ?? null;
        this.charliealphaFunds = response.results?.results ?? [];
        this.charliealphaTotalCount = response.count ?? this.charliealphaFunds.length;
        this.charliealphaNext = response.next;
        this.charliealphaPrevious = response.previous;
        this.charliealphaAmcs = response.results?.filters?.amcs ?? [];
        this.charliealphaCategories = response.results?.filters?.categories ?? [];
        this.charliealphaPlans = response.results?.filters?.plans ?? [];
        this.charliealphaOptions = response.results?.filters?.options ?? [];
        if (this.charliealphaSelectedCode && !this.charliealphaFunds.some(row => row["scheme_code"] === this.charliealphaSelectedCode)) {
          this.charliealphaSelectedCode = '';
          this.charliealphaDetail = null;
        }
        if (!this.charliealphaSelectedCode && this.charliealphaFunds.length) {
          this.charliealphaSelectedCode = String(this.charliealphaFunds[0].scheme_code ?? '');
        }
        this.charliealphaLoading = false;
        this.cdr.detectChanges();
        if (this.charliealphaSelectedCode) this.loadCharlieAlphaFund(this.charliealphaSelectedCode);
        this.refreshCharts();
      },
      error: error => {
        this.charliealphaLoading = false;
        this.charliealphaError = error?.status === 401
          ? 'Sign in again to access stored CharlieAlpha intelligence.'
          : 'CharlieAlpha data is not available from the PWMS database yet. Run the synchronization command, then retry.';
        this.cdr.detectChanges();
      },
    });
  }

  loadCharlieAlphaFund(code: string): void {
    if (!code) return;
    this.charliealphaSelectedCode = code;
    this.charliealphaDetailLoading = true;
    this.charliealphaDetailError = '';
    this.charliealphaFundSubscription?.unsubscribe();
    this.charliealphaFundSubscription = this.charliealphaApi.getFund(code, {
      navLimit: 3000,
      fromDate: this.charliealphaFromDate || undefined,
      toDate: this.charliealphaToDate || undefined,
      holdingsAsOf: this.charliealphaHoldingDate || undefined,
    }).subscribe({
      next: detail => {
        this.charliealphaDetail = detail;
        this.charliealphaDetailLoading = false;
        this.cdr.detectChanges();
        this.refreshCharts();
      },
      error: error => {
        this.charliealphaDetail = null;
        this.charliealphaDetailLoading = false;
        this.charliealphaDetailError = error?.status === 404
          ? 'No CharlieAlpha-linked scheme detail was found for this scheme code.'
          : 'Unable to load the selected scheme from stored CharlieAlpha data.';
        this.cdr.detectChanges();
      },
    });
  }

  loadCharlieAlphaBenchmarks(): void {
    this.charliealphaApi.getBenchmarks().subscribe({
      next: response => {
        this.charliealphaBenchmarks = response.results ?? [];
        if (!this.charliealphaBenchmarkSlug && this.charliealphaBenchmarks.length) {
          this.charliealphaBenchmarkSlug = String(this.charliealphaBenchmarks[0].slug ?? '');
        }
        if (this.charliealphaBenchmarkSlug) this.selectCharlieAlphaBenchmark(this.charliealphaBenchmarkSlug);
      },
      error: () => {
        this.charliealphaBenchmarks = [];
      },
    });
  }

  selectCharlieAlphaBenchmark(slug: string): void {
    if (!slug) return;
    this.charliealphaBenchmarkSlug = slug;
    this.charliealphaApi.getBenchmarks(slug).subscribe({
      next: response => {
        this.charliealphaBenchmark = response.results?.[0] ?? null;
        this.cdr.detectChanges();
        this.refreshCharts();
      },
      error: () => {
        this.charliealphaBenchmark = null;
        this.cdr.detectChanges();
      },
    });
  }

  toggleCharlieAlphaCompare(code: string): void {
    if (this.charliealphaCompareCodes.includes(code)) {
      this.charliealphaCompareCodes = this.charliealphaCompareCodes.filter(item => item !== code);
    } else if (this.charliealphaCompareCodes.length < 20) {
      this.charliealphaCompareCodes = [...this.charliealphaCompareCodes, code];
    } else {
      this.charliealphaCompareError = 'The provider accepts a maximum of 20 schemes per comparison.';
    }
    this.charliealphaCompareResult = null;
  }

  get maxCharlieAlphaPage(): number {
    return Math.max(1, Math.ceil(this.charliealphaTotalCount / this.charliealphaPageSize));
  }

  get sortedCharlieAlphaFunds(): Array<Record<string, any>> {
    const field = this.charliealphaSortField;
    const direction = this.charliealphaSortDirection === "asc" ? 1 : -1;
    return [...this.charliealphaFunds].sort((left, right) => {
      const a = field === "ter_pct" ? (left?.["ter_direct_pct"] ?? left?.["ter_regular_pct"]) : left?.[field];
      const b = field === "ter_pct" ? (right?.["ter_direct_pct"] ?? right?.["ter_regular_pct"]) : right?.[field];
      if (a === null || a === undefined || a === "") return 1;
      if (b === null || b === undefined || b === "") return -1;
      if (typeof a === "number" || typeof b === "number") {
        return (Number(a) - Number(b)) * direction;
      }
      return String(a).localeCompare(String(b), undefined, { sensitivity: "base" }) * direction;
    });
  }

  sortCharlieAlphaBy(field: string): void {
    if (this.charliealphaSortField === field) {
      this.charliealphaSortDirection = this.charliealphaSortDirection === "asc" ? "desc" : "asc";
    } else {
      this.charliealphaSortField = field;
      this.charliealphaSortDirection = "desc";
    }
  }

  applyCharlieAlphaFilters(): void {
    this.charliealphaPage = 1;
    this.loadCharlieAlphaOverview();
  }

  goToCharlieAlphaPage(page: number): void {
    const lastPage = Math.max(1, Math.ceil(this.charliealphaTotalCount / this.charliealphaPageSize));
    this.charliealphaPage = Math.min(lastPage, Math.max(1, page));
    this.loadCharlieAlphaOverview();
  }

  isCharlieAlphaCompared(code: string): boolean {
    return this.charliealphaCompareCodes.includes(code);
  }

  runCharlieAlphaCompare(overlapOnly = false): void {
    if (this.charliealphaCompareCodes.length < 2) {
      this.charliealphaCompareError = 'Select at least two funds in the universe table.';
      return;
    }
    this.charliealphaCompareLoading = true;
    this.charliealphaCompareError = '';
    this.charliealphaCompareResult = null;
    this.charliealphaCompareSubscription?.unsubscribe();
    this.charliealphaCompareSubscription = this.charliealphaApi.compare(this.charliealphaCompareCodes, overlapOnly).subscribe({
      next: response => {
        this.charliealphaCompareResult = response.result ?? response;
        this.charliealphaCompareLoading = false;
        this.cdr.detectChanges();
      },
      error: error => {
        this.charliealphaCompareLoading = false;
        this.charliealphaCompareError = error?.error?.detail ?? 'No stored comparison result is available for this fund selection yet. Run the matching sync_charliealpha compare/overlap command first.';
        this.cdr.detectChanges();
      },
    });
  }

  applyCharlieAlphaDateRange(): void {
    if (this.charliealphaSelectedCode) this.loadCharlieAlphaFund(this.charliealphaSelectedCode);
  }

  getCharlieAlphaPeriodDate(period: any): string {
    if (typeof period === 'string') return period;
    return String(period?.as_of ?? period?.report_date ?? period?.date ?? '');
  }

  selectCharlieAlphaHoldingDate(value: string): void {
    this.charliealphaHoldingDate = value;
    if (this.charliealphaSelectedCode) this.loadCharlieAlphaFund(this.charliealphaSelectedCode);
  }

  loadCharlieAlphaExposure(): void {
    const identifier = this.charliealphaExposureIdentifier.trim();
    if (!identifier) {
      this.charliealphaExposureError = 'Enter a stock ISIN, NSE symbol or BSE scrip code.';
      return;
    }
    this.charliealphaExposureLoading = true;
    this.charliealphaExposureError = '';
    this.charliealphaExposureResult = null;
    this.charliealphaApi.getExposure(identifier).subscribe({
      next: result => {
        this.charliealphaExposureResult = result?.result ?? result;
        this.charliealphaExposureLoading = false;
        this.cdr.detectChanges();
      },
      error: error => {
        this.charliealphaExposureError = error?.error?.detail ?? 'No stored exposure data found. Run the exposure ingestion command first.';
        this.charliealphaExposureLoading = false;
        this.cdr.detectChanges();
      },
    });
  }

  private renderCharlieAlphaCharts(): void {
    const returnsCanvas = document.getElementById('charliealpha-returns-chart') as HTMLCanvasElement | null;
    const returnRows = this.charliealphaFunds
      .filter(row => row["return_1y_pct"] !== null && row["return_1y_pct"] !== undefined)
      .sort((a, b) => Number(b["return_1y_pct"]) - Number(a["return_1y_pct"]))
      .slice(0, 15);
    if (returnsCanvas && returnRows.length) {
      const chart = new Chart(returnsCanvas, {
        type: 'bar',
        data: {
          labels: returnRows.map(row => row["scheme_name"] ?? row["scheme_code"]),
          datasets: [{ label: '1Y trailing CAGR (%)', data: returnRows.map(row => Number(row["return_1y_pct"])), backgroundColor: '#FD7740' }],
        },
        options: { ...this.chartOptions(), indexAxis: 'y' },
      });
      this.charts.push(chart);
    }

    const riskCanvas = document.getElementById('charliealpha-risk-chart') as HTMLCanvasElement | null;
    const riskRows = this.charliealphaFunds.filter(row =>
      row["std_dev_pct"] !== null && row["std_dev_pct"] !== undefined &&
      row["return_3y_pct"] !== null && row["return_3y_pct"] !== undefined
    );
    if (riskCanvas && riskRows.length) {
      const chart = new Chart(riskCanvas, {
        type: 'scatter',
        data: {
          datasets: [{
            label: 'Mutual funds',
            data: riskRows.map(row => ({
              x: Number(row["std_dev_pct"]),
              y: Number(row["return_3y_pct"]),
              fund: String(row["scheme_name"] ?? row["scheme_code"]),
            })),
            backgroundColor: '#315b83',
            pointRadius: 5,
          }],
        },
        options: {
          ...this.chartOptions(),
          plugins: {
            ...this.chartOptions().plugins,
            tooltip: {
              callbacks: {
                label: (context: any) => {
                  const point = context.raw;
                  return String(point.fund) + ': volatility ' + Number(point.x).toFixed(2) +
                    '%, 3Y CAGR ' + Number(point.y).toFixed(2) + '%';
                },
              },
            },
          },
          scales: {
            x: { title: { display: true, text: 'Annualized volatility (%)' } },
            y: { title: { display: true, text: '3Y trailing CAGR (%)' } },
          },
        },
      } as any);
      this.charts.push(chart);
    }

    const navCanvas = document.getElementById('charliealpha-nav-chart') as HTMLCanvasElement | null;
    const navRows = this.charliealphaDetail?.nav_history ?? [];
    if (navCanvas && navRows.length) {
      const chart = new Chart(navCanvas, {
        type: 'line',
        data: {
          labels: navRows.map(row => row.date),
          datasets: [{ label: 'CharlieAlpha NAV (₹)', data: navRows.map(row => Number(row.nav)), borderColor: '#315b83', pointRadius: 0, fill: false, tension: 0.15 }],
        },
        options: { ...this.chartOptions(), scales: { ...this.chartOptions().scales, y: { ...this.chartOptions().scales.y, title: { display: true, text: 'NAV (₹)' } } } },
      });
      this.charts.push(chart);
    }

    const holdingsCanvas = document.getElementById('charliealpha-holdings-chart') as HTMLCanvasElement | null;
    const holdings = (this.charliealphaDetail?.holdings ?? [])
      .filter(row => row["weight_pct"] !== null && row["weight_pct"] !== undefined)
      .sort((a, b) => Number(b.weight_pct) - Number(a.weight_pct))
      .slice(0, 12);
    if (holdingsCanvas && holdings.length) {
      const chart = new Chart(holdingsCanvas, {
        type: 'bar',
        data: {
          labels: holdings.map(row => row["name"]),
          datasets: [{ label: 'Portfolio weight (%)', data: holdings.map(row => Number(row["weight_pct"])), backgroundColor: '#CC9F53' }],
        },
        options: { ...this.chartOptions(), indexAxis: 'y' },
      } as any);
      this.charts.push(chart);
    }

    const sectorCanvas = document.getElementById('charliealpha-sector-chart') as HTMLCanvasElement | null;
    const sectors = Object.entries(this.charliealphaDetail?.snapshot?.["sector_allocation"] ?? {})
      .map(([name, value]) => ({ name, value: Number(value) }))
      .filter(row => Number.isFinite(row.value) && row.value > 0)
      .sort((a, b) => b.value - a.value)
      .slice(0, 12);
    if (sectorCanvas && sectors.length) {
      const chart = new Chart(sectorCanvas, {
        type: 'bar',
        data: {
          labels: sectors.map(row => row["name"]),
          datasets: [{ label: 'Sector weight (%)', data: sectors.map(row => row.value), backgroundColor: '#315b83' }],
        },
        options: { ...this.chartOptions(), indexAxis: 'y' },
      } as any);
      this.charts.push(chart);
    }

    const benchmarkCanvas = document.getElementById('charliealpha-benchmark-chart') as HTMLCanvasElement | null;
    const benchmarkSeries = this.charliealphaBenchmark?.series ?? [];
    if (benchmarkCanvas && benchmarkSeries.length) {
      const chart = new Chart(benchmarkCanvas, {
        type: 'line',
        data: {
          labels: benchmarkSeries.map((row: any) => row.date),
          datasets: [{ label: (this.charliealphaBenchmark.name ?? this.charliealphaBenchmark.slug) + ' — provider normalized', data: benchmarkSeries.map((row: any) => Number(row.normalized_value)), borderColor: '#CC9F53', pointRadius: 0, tension: 0.15 }],
        },
        options: { ...this.chartOptions(), scales: { ...this.chartOptions().scales, y: { ...this.chartOptions().scales.y, title: { display: true, text: 'Normalized provider value (base 100)' } } } },
      });
      this.charts.push(chart);
    }
  }

}
