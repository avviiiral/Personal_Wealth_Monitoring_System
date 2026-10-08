import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { AnalyticsComponent } from './analytics.component';
import { WealthApiService } from '../../core/services/wealth-api.service';

describe('AnalyticsComponent', () => {
  let component: AnalyticsComponent;
  let fixture: ComponentFixture<AnalyticsComponent>;

  let wealthApi: {
    getAnalyticsDashboard: ReturnType<typeof vi.fn>;
  };

  const mockSummary = {
    total_invested: 100000,
    total_current_value: 125000,
    total_pnl: 25000,
    unrealized_pnl: 20000,
    realized_pnl: 5000,
    return_percentage: 25,
    xirr_percentage: 18.5,
    number_of_holdings: 3,
  };

  const mockInvestmentSummary = {
    results: [
      {
        asset_category: 'Equities',
        asset_class: 'Direct Equity',
        current_value: 75000,
        percentage_of_total: 60,
        raw_asset_classes: ['Direct Equity'],
      },
      {
        asset_category: 'Fixed Income',
        asset_class: 'Debt Mutual Fund',
        current_value: 50000,
        percentage_of_total: 40,
        raw_asset_classes: ['Debt Mutual Fund'],
      },
    ],
    total_current_value: 125000,
  };

  const mockDashboard = {
    summary: mockSummary,
    investment_summary: mockInvestmentSummary,
    dashboard_investment_summary: [],
    allocation: {
      results: [
        { category: 'Equities', value: 75000, percentage: 60 },
        { category: 'Fixed Income', value: 50000, percentage: 40 },
      ],
    },
    performance: {
      results: [
        {
          asset_name: 'Direct Equity Asset',
          asset_class: 'Direct Equity',
          asset_category: 'Equities',
          xirr_percentage: 30,
          underlying: 'Direct Equity Asset',
        },
        {
          asset_name: 'Debt Fund Asset',
          asset_class: 'Debt Mutual Fund',
          asset_category: 'Fixed Income',
          xirr_percentage: 10,
          underlying: 'Debt Fund Asset',
        },
      ],
    },
    advisor_allocation: {
      results: [
        { advisor: 'Advisor A', value: 75000, percentage: 60 },
        { advisor: 'Unassigned', value: 50000, percentage: 40 },
      ],
    },
    advisor_performance: {
      results: [
        { advisor: 'Advisor A', invested_value: 60000, current_value: 75000, unrealized_pnl: 15000, pnl_percentage: 25 },
        { advisor: 'Unassigned', invested_value: 45000, current_value: 50000, unrealized_pnl: 5000, pnl_percentage: 11.11 },
      ],
    },
    xirr: { xirr_percentage: 18.5 },
    historical: {
      days: 30,
      results: [
        { date: '2026-01-01', invested_value: 90000, portfolio_value: 100000, pnl: 10000 },
        { date: '2026-01-30', invested_value: 100000, portfolio_value: 125000, pnl: 25000 },
      ],
    },
    market_cap_allocation: { results: [] },
    sector_allocation: { results: [] },
    insights: {
      best_performer: {
        asset_name: 'Direct Equity Asset',
        asset_class: 'Direct Equity',
        xirr_percentage: 30,
      },
      worst_performer: {
        asset_name: 'Debt Fund Asset',
        asset_class: 'Debt Mutual Fund',
        xirr_percentage: 10,
      },
      largest_allocation: {
        category: 'Equities',
        percentage: 60,
      },
      period_value_change: 25,
    },
    portfolio_tree: { success: true, count: 0, families: [] },
    standard_allocations: {},
  };

  beforeEach(async () => {
    wealthApi = {
      getAnalyticsDashboard: vi.fn().mockReturnValue(of(mockDashboard)),
    };

    await TestBed.configureTestingModule({
      imports: [AnalyticsComponent],
      providers: [
        {
          provide: WealthApiService,
          useValue: wealthApi,
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AnalyticsComponent);
    component = fixture.componentInstance;

    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should load analytics data from the backend view model', () => {
    expect(component.summary).toEqual(mockSummary);
    expect(component.investmentSummary).toEqual(mockInvestmentSummary);
    expect(component.allocation).toEqual(mockDashboard.allocation);
    expect(component.performance).toEqual(mockDashboard.performance);
    expect(component.advisorAllocation).toEqual(mockDashboard.advisor_allocation);
    expect(component.advisorPerformance).toEqual(mockDashboard.advisor_performance);
    expect(component.xirr).toEqual(mockDashboard.xirr);
    expect(component.historical).toEqual(mockDashboard.historical);
  });

  it('should use backend insight values without recalculating them in the component', () => {
    expect(component.getBestPerformerName()).toBe('Direct Equity');
    expect(component.getBestPerformerReturn()).toBe(30);
    expect(component.getWorstPerformerName()).toBe('Debt Mutual Fund');
    expect(component.getWorstPerformerReturn()).toBe(10);
    expect(component.getLargestAllocationName()).toBe('Equities');
    expect(component.getLargestAllocationPercentage()).toBe(60);
    expect(component.periodValueChange).toBe(25);
  });

  it('should change historical period through the aggregated API', () => {
    component.changePeriod(90);

    expect(component.selectedDays).toBe(90);
    expect(wealthApi.getAnalyticsDashboard).toHaveBeenLastCalledWith('90d', 90);
  });

  it('should not reload when selecting the same period', () => {
    wealthApi.getAnalyticsDashboard.mockClear();

    component.changePeriod(30);

    expect(wealthApi.getAnalyticsDashboard).not.toHaveBeenCalled();
  });

  it('should handle API errors', () => {
    wealthApi.getAnalyticsDashboard.mockReturnValue(
      throwError(() => ({
        status: 500,
      })),
    );

    component.loadAnalytics();

    expect(component.loading).toBe(false);
    expect(component.error).toBe('Unable to load analytics data. Please refresh and try again.');
  });

  it('should format currency', () => {
    expect(component.formatCurrency(125000)).toContain('₹');
    expect(component.formatCurrency(125000)).toContain('1,25,000');
  });

  it('should format category names', () => {
    expect(component.formatCategory('MUTUAL_FUND')).toBe('Mutual Fund');
  });

  it('should format dates', () => {
    expect(component.formatDate('2026-01-01')).toBe('01 Jan');
  });
});
