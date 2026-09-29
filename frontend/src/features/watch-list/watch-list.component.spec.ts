import { ComponentFixture, TestBed } from '@angular/core/testing';

import { WatchListApiService } from '../../core/services/watch-list-api.service';
import { WatchListStateService } from '../../core/services/watch-list-state.service';
import { WatchListComponent } from './watch-list.component';

describe('WatchListComponent benchmark chart', () => {
  let fixture: ComponentFixture<WatchListComponent>;
  let component: WatchListComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [WatchListComponent],
      providers: [
        { provide: WatchListApiService, useValue: {} },
        { provide: WatchListStateService, useValue: {} },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(WatchListComponent);
    component = fixture.componentInstance;
  });

  afterEach(() => {
    fixture.destroy();
  });

  function setChart(productType: 'MUTUAL_FUND' | 'PMS'): void {
    component.benchmarkModalProduct = {
      id: 1,
      name: productType === 'PMS' ? 'Test PMS' : 'Test Fund',
      product_type: productType,
      provider: 'Test Provider',
      category: 'Test',
      benchmark: 'Nifty 50',
      status: 'UNIVERSAL',
      is_watchlisted: false,
      metrics: {},
      performance: [],
      ownership: [],
      mutual_fund: productType === 'MUTUAL_FUND' ? {} : undefined,
      pms: productType === 'PMS' ? {} : undefined,
    } as any;
    component.benchmarkPeriod = '1Y';
    component.benchmarkData = {
      available: true,
      benchmark: 'Nifty 50',
      chart: {
        start_date: '2025-09-15',
        end_date: '2026-09-15',
        aligned_points: [
          { date: '2025-09-15', product_value: 100, benchmark_value: 25000 },
          { date: '2026-09-15', product_value: 110, benchmark_value: 27000 },
        ],
        fund: [
          { date: '2025-09-15', value: 100 },
          { date: '2026-09-15', value: 110 },
        ],
        benchmark: [
          { date: '2025-09-15', value: 25000 },
          { date: '2026-09-15', value: 27000 },
        ],
      },
    };
  }

  it('indexes mutual-fund NAV and benchmark from a shared base of 100', () => {
    setChart('MUTUAL_FUND');

    expect(component.benchmarkChartProductUnit()).toBe('NAV');
    expect(component.benchmarkChartHasData()).toBe(true);
    expect(component.benchmarkChartRange().min).toBeLessThan(100);
    expect(component.benchmarkChartRange().max).toBeGreaterThan(108);
    expect(component.benchmarkChartYTicks().length).toBeGreaterThan(1);

    const product = component.benchmarkPolyline('product');
    const benchmark = component.benchmarkPolyline('benchmark');
    expect(product).toContain(',');
    expect(benchmark).toContain(',');
  });

  it('renders PMS NAV/value and actual plus indexed values in the tooltip', () => {
    setChart('PMS');

    expect(component.benchmarkChartProductUnit()).toBe('NAV / Value');
    const points = component.benchmarkChartCirclePoints('product');

    expect(points[0].title).toContain('Test PMS — NAV / Value: 100.00');
    expect(points[0].title).toContain('Index 100.000');
    expect(points[0].title).toContain('Nifty 50: 25,000.00');
    expect(points[1].title).toContain('2026-09-15');
    expect(points[1].title).toContain('Index 110.000');
  });

  it('formats historical dates for the X axis across the selected period', () => {
    setChart('MUTUAL_FUND');

    const ticks = component.benchmarkChartXAxisTicks();

    expect(ticks.length).toBe(5);
    expect(ticks[0].label).toContain('Sep');
    expect(ticks[0].label).toContain('2025');
    expect(ticks[ticks.length - 1].label).toContain('Sep');
    expect(ticks[ticks.length - 1].label).toContain('2026');
  });
});
