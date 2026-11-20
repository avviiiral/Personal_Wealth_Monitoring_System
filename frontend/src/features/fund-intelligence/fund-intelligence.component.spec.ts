import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { FundIntelligenceComponent } from './fund-intelligence.component';
import {
  FundIntelligenceApiService,
  FundIntelligenceSnapshot,
  ReturnMap,
} from '../../core/services/fund-intelligence-api.service';

// Fixture values are arbitrary test inputs, not market data.
const NO_RETURNS: ReturnMap = {
  '1m': null, '3m': null, '6m': null, '1y': null, '3y': null, '5y': null, since_inception: null, cagr: null,
};
const CATEGORY_1Y = 8; // category average 1Y used for the fixture
const FUND_1Y = 10; // fixture fund 1Y return, 2 points above the category
const FUND_AUM = 5000; // stored AUM in the fixture's own units
const FUND_EXPENSE_RATIO = 1.7; // percent

function buildSnapshot(overrides: Partial<FundIntelligenceSnapshot> = {}): FundIntelligenceSnapshot {
  return {
    meta: {
      category: 'Multi Cap',
      plan: 'Regular',
      option: 'Growth',
      fund_count: 1,
      stored_data_as_of: '2026-09-30',
      generated_at: '2026-11-20T09:00:00Z',
    },
    category_average: { ...NO_RETURNS, '1y': CATEGORY_1Y },
    funds: [
      {
        product_id: 1,
        name: 'HSBC Multi Cap Fund - Regular Plan - Growth',
        short_name: 'HSBC Multi Cap Fund',
        provider: 'HSBC',
        category: 'Equity Scheme - Multi Cap Fund',
        isin: null,
        plan: 'Regular Plan',
        option: 'Growth Option',
        fund_manager: 'Test Manager',
        benchmark: null,
        aum: FUND_AUM,
        expense_ratio: FUND_EXPENSE_RATIO,
        exit_load: '1%',
        risk_level: null,
        minimum_investment: null,
        latest_nav: null,
        latest_nav_date: null,
        as_of: '2026-09-30',
        returns: { ...NO_RETURNS, '1y': FUND_1Y },
        vs_category: { ...NO_RETURNS, '1y': FUND_1Y - CATEGORY_1Y },
        live: null,
      },
    ],
    live: {
      status: 'not_configured',
      message: 'INDIAN_API_KEY is not set',
      fetched_at: null,
      records_received: 0,
      matched: 0,
      unmatched: ['HSBC Multi Cap Fund - Regular Plan - Growth'],
    },
    data_coverage: { available: ['Returns'], not_available: [{ item: 'Holdings', reason: 'Not stored' }] },
    ...overrides,
  };
}

function createComponent(getSnapshot: () => ReturnType<FundIntelligenceApiService['getSnapshot']>): ComponentFixture<FundIntelligenceComponent> {
  TestBed.configureTestingModule({
    imports: [FundIntelligenceComponent],
    providers: [{ provide: FundIntelligenceApiService, useValue: { getSnapshot } }],
  });
  const fixture = TestBed.createComponent(FundIntelligenceComponent);
  fixture.detectChanges();
  return fixture;
}

describe('FundIntelligenceComponent', () => {
  it('renders the stored snapshot with its fund name and AUM', () => {
    const fixture = createComponent(() => of(buildSnapshot()));
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    expect(text).toContain('HSBC Multi Cap Fund');
    expect(text).toContain('Test Manager');
  });

  it('shows the not-configured banner when the live key is absent', () => {
    const fixture = createComponent(() => of(buildSnapshot()));
    const banner = (fixture.nativeElement as HTMLElement).querySelector('.fi-live');

    expect(banner?.classList).toContain('fi-live--not_configured');
    expect(banner?.textContent).toContain('Live enrichment is off');
  });

  it('shows the error banner text when live enrichment fails', () => {
    const snapshot = buildSnapshot({
      live: { status: 'error', message: 'HTTP 429', fetched_at: null, records_received: 0, matched: 0, unmatched: [] },
    });
    const fixture = createComponent(() => of(snapshot));
    const banner = (fixture.nativeElement as HTMLElement).querySelector('.fi-live');

    expect(banner?.classList).toContain('fi-live--error');
    expect(banner?.textContent).toContain('HTTP 429');
  });

  it('renders an empty-state row when the universe has no funds', () => {
    const snapshot = buildSnapshot({
      funds: [],
      meta: { ...buildSnapshot().meta, fund_count: 0 },
    });
    const fixture = createComponent(() => of(snapshot));
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    expect(text).toContain('No funds match the Multi Cap');
    expect(text).not.toContain('Best 1Y');
  });

  it('shows the error message when the request fails', () => {
    const fixture = createComponent(() => throwError(() => new Error('network down')));
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    expect(text).toContain('Could not load Fund Intelligence data');
  });
});
