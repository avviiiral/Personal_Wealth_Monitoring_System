import { PLATFORM_ID } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { MfDashboard, MfFund, MfIntelligenceApiService } from '../../core/services/mf-intelligence-api.service';
import { RbacService } from '../../core/services/rbac.service';
import { MfIntelligenceComponent } from './mf-intelligence.component';

function fund(code: string, name: string, oneYear: number | null, score: number | null, extra: Partial<MfFund> = {}): MfFund {
  return {
    code,
    name: `${name} - Regular Plan - Growth`,
    short_name: name,
    amc: 'Test AMC',
    plan: 'Regular',
    option: 'Growth',
    isin: '',
    age_label: '5Y, 0M',
    age_months: 60,
    first_nav_date: '2021-01-01',
    aum: 1000,
    aum_period: 'April–June 2026',
    ter: 1.5,
    nav: 20,
    nav_date: '2026-11-23',
    all_time_mdd: -20,
    returns: { '1m': 1, '3m': 2, '6m': 3, ytd: 4, '1y': oneYear, '3y': 12 },
    calendar: { '2024': 20, '2025': 5 },
    metrics: { '3Y': { sharpe_ratio: 1, trailing_max_drawdown: -15, beta: 0.9, upside_capture: 100, downside_capture: 90 } },
    calmar: { '3Y': 0.8 },
    holdings: null,
    holdings_from_code: null,
    unique: { count: 0, weight_pct: 0, share_of_equity_pct: null },
    score: { total: score, components: { returns: score }, rank: undefined },
    ...extra,
  };
}

function dashboard(funds: MfFund[]): MfDashboard {
  return {
    category: { slug: 'multi-cap', name: 'Equity Scheme - Multi Cap Fund', asset_class: 'Equity', peer_count_all_plans: 6, metrics_as_of: '2026-11-23', metrics_synced_at: null, total_schemes: 6 },
    plan: 'Regular',
    plans: ['Regular', 'Direct'],
    horizon: '3Y',
    horizons: ['1Y', '3Y', '5Y', '10Y'],
    as_of: { nav: '2026-11-23', metrics: '2026-11-23', holdings: null },
    benchmark: { slug: 'nifty-500-tri', name: 'NIFTY 500 TRI', asset_type: 'Equity', latest_date: '2026-11-23', returns: { '1y': 5, ytd: 2 }, calendar: { '2024': 15, '2025': 6 } },
    benchmarks: [{ slug: 'nifty-500-tri', name: 'NIFTY 500 TRI', asset_type: 'Equity' }],
    calendar_years: ['2024', '2025'],
    funds,
    category_average: {
      aum: 1000, ter: 1.5, age_months: 60, all_time_mdd: -20,
      returns: { '1y': 8 }, calendar: { '2024': 20 }, metrics: { '3Y': { sharpe_ratio: 1 } }, calmar: { '3Y': 0.8 },
      score: { total: 50, components: {} }, holdings: null,
    },
    category_median_all_plans: {},
    activity: { new_entries: [], full_exits: [], net_buys: [], net_sells: [], sector_change: [] },
    cap_classification: { amfi_period_end: '2026-06-30', amfi_rows: 10, index_proxy_rows: 0, method: 'AMFI' },
    flags: { has_holdings: false, has_equity: false, has_fixed_income: false, has_returns: true, has_metrics: true },
    score_methodology: [{ key: 'returns', weight: 25, source: 'trailing_cagr', higher_is_better: true }],
    sector_styles: {},
    last_sync: null,
  };
}

describe('MfIntelligenceComponent', () => {
  let fixture: ComponentFixture<MfIntelligenceComponent>;
  let component: MfIntelligenceComponent;
  let api: Record<string, ReturnType<typeof vi.fn>>;
  let role: string | null;

  beforeEach(async () => {
    role = 'ADMIN';
    api = {
      getCategories: vi.fn(() => of({ groups: [], last_sync: null, today: '2026-11-24' })),
      getDashboard: vi.fn(),
      getScheme: vi.fn(() => of(null)),
      getSyncStatus: vi.fn(() => of({ running: false, run: null })),
      startSync: vi.fn(),
    };
    await TestBed.configureTestingModule({
      imports: [MfIntelligenceComponent],
      providers: [
        { provide: MfIntelligenceApiService, useValue: api },
        { provide: RbacService, useValue: { role: () => role } },
        // Chart.js needs a real canvas; render templates without charts.
        { provide: PLATFORM_ID, useValue: 'server' },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(MfIntelligenceComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => fixture.destroy());

  it('shows the empty state when nothing has been synced', () => {
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('No mutual fund data yet');
    expect(text).toContain('sync_mf_intelligence');
  });

  it('selects the highest Quant Score fund and ranks peers', () => {
    const funds = [fund('1', 'Alpha', 10, 40), fund('2', 'Beta', 20, 90), fund('3', 'Gamma', null, null)];
    component.applyDashboard(dashboard(funds));
    expect(component.activeCode).toBe('2');
    expect(component.fund?.short_name).toBe('Beta');
    expect(component.performanceRows.map((item) => item.code)).toEqual(['2', '1', '3']);
    expect(component.returnRank(funds[0], '1y')).toBe(2);
    expect(component.returnQuartile(funds[1], '1y')).toBe('Q2');
    expect(component.countWith('1y')).toBe(2);
    expect(api['getScheme']).toHaveBeenCalledWith('2', 'nifty-500-tri');
  });

  it('keeps the selected fund when the dashboard reloads', () => {
    const funds = [fund('1', 'Alpha', 10, 40), fund('2', 'Beta', 20, 90)];
    component.applyDashboard(dashboard(funds));
    component.selectFund('1');
    component.applyDashboard(dashboard(funds));
    expect(component.activeCode).toBe('1');
  });

  it('writes takeaways from the data and escapes fund names', () => {
    const funds = [fund('1', 'Alpha <x>', 10, 40), fund('2', 'Beta', -2, 90)];
    component.applyDashboard(dashboard(funds));
    const overview = component.summaries.overview?.html ?? '';
    expect(overview).toContain('<strong>1 of 2</strong> funds have a positive 1Y return');
    expect(overview).toContain('Alpha &lt;x&gt;');
    expect(overview).not.toContain('<x>');
  });

  it('sorts the universe table by any column and toggles direction', () => {
    const funds = [fund('1', 'Alpha', 10, 40), fund('2', 'Beta', 20, 90), fund('3', 'Gamma', null, 60)];
    component.applyDashboard(dashboard(funds));
    expect(component.universeRows.map((item) => item.code)).toEqual(['2', '3', '1']);
    component.sortUniverse('r_1y');
    expect(component.universeRows.map((item) => item.code)).toEqual(['2', '1', '3']);
    component.sortUniverse('r_1y');
    expect(component.universeRows.map((item) => item.code)).toEqual(['1', '2', '3']); // missing values stay last
  });

  it('counts calendar years with positive excess return', () => {
    const funds = [fund('1', 'Alpha', 10, 40)];
    component.applyDashboard(dashboard(funds));
    expect(component.positiveAlphaYears(funds[0])).toEqual({ positive: 1, total: 2 });
  });

  it('handles an empty peer group without errors', () => {
    component.applyDashboard(dashboard([]));
    expect(component.fund).toBeNull();
    expect(component.activeCode).toBe('');
    expect(component.summaries.overview?.html).toContain('No synced data');
  });

  it('only offers the sync button to roles that can write', () => {
    role = 'VIEWER';
    expect(component.canSync).toBe(false);
    role = null;
    expect(component.canSync).toBe(false);
    role = 'SUPERUSER';
    expect(component.canSync).toBe(true);
  });

  it('starts a sync for the selected category and plan', () => {
    api['startSync'].mockReturnValue(of({ status: 'started', run: { id: 1, status: 'running' } }));
    component.categorySlug = 'multi-cap';
    component.toggleSyncPanel();
    component.syncPlan = 'Both';
    component.startSync();
    expect(api['startSync']).toHaveBeenCalledWith({ category: 'multi-cap', plan: 'Both', force: false });
    expect(component.syncRunning).toBe(true);
    expect(component.syncPanelOpen).toBe(false);
  });

  it('explains a forbidden sync', () => {
    api['startSync'].mockReturnValue(throwError(() => ({ status: 403 })));
    component.startSync();
    expect(component.syncMessage).toContain('Admin or above');
  });

  it('reports a dashboard load failure', () => {
    api['getDashboard'].mockReturnValue(throwError(() => new Error('down')));
    component.categorySlug = 'multi-cap';
    component.loadDashboard();
    expect(component.error).toContain('Could not load');
    expect(component.loading).toBe(false);
  });
});
