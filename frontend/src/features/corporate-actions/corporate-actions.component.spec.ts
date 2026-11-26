import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { CorporateActionsService } from '../../app/core/services/corporate-actions.service';
import { CorporateActionsComponent } from './corporate-actions.component';

const NOTIFICATION_ID = 7;
const ACTION_ID = 3;
const RECONCILIATION_ID = 11;
const SHOWN_FINGERPRINT = 'fingerprint-shown-to-user';
const ISIN = 'INE000ABC011';
const MEMBER_PENDING = 'Member A';
const MEMBER_RECORDED = 'Member B';
const UPLOADED_TRANSACTION_ID = 90;
const CREATED_TRANSACTION_ID = 101;
const HTTP_CONFLICT = 409;
const STALE_MESSAGE = 'The calculation changed.';
const EX_DATE = '2025-06-15';

function pendingPosition() {
  return {
    id: 1, status: 'PENDING', family_id: 1, family_name: MEMBER_PENDING, portfolio: 'Direct Equity',
    asset_class: 'Equity', sub_class: 'Listed', asset_name: 'Direct Equity', underlying: 'ABC Ltd', advisors: '',
    asset_ids: [5], eligibility_cutoff: EX_DATE, eligible_quantity: '100', quantity_delta: '100',
    resulting_quantity: '200', new_security_quantity: null, fractional_entitlement: null, cost_basis: '1000',
    resulting_cost_basis: '1000', allocated_cost: null, cash_amount: null, matched_transaction_ids: [],
    matched_transactions: [], created_transaction_ids: [],
    planned_entries: [{ role: 'SOURCE', transaction_type: 'SPLIT', transaction_date: EX_DATE, quantity: '100',
      price_per_unit: '0', amount: '0', label: 'Split adjustment', asset_id: 5, asset_label: 'ABC Ltd', underlying: 'ABC Ltd' }],
    explanation: '100 shares × 2/1 (split) = 200.', eligibility_rule: `Trades dated before ${EX_DATE} are entitled (ex-date rule).`,
    cash_in_lieu_pending: false, reason: '', applied_at: null,
  };
}

function recordedPosition() {
  return {
    ...pendingPosition(), id: 2, status: 'ALREADY_RECORDED', family_name: MEMBER_RECORDED, portfolio: 'Broker',
    eligible_quantity: '50', quantity_delta: null, resulting_quantity: null, cost_basis: '500',
    matched_transaction_ids: [UPLOADED_TRANSACTION_ID],
    matched_transactions: [{ id: UPLOADED_TRANSACTION_ID, transaction_type: 'SPLIT', transaction_date: EX_DATE,
      quantity: '50', amount: '0', source: 'EXCEL', portfolio: 'Broker' }],
    planned_entries: [], explanation: '', eligibility_rule: '',
    reason: `Already recorded by existing transaction(s) #${UPLOADED_TRANSACTION_ID}.`,
  };
}

function inboxItem(overrides: Record<string, unknown> = {}) {
  return {
    id: NOTIFICATION_ID,
    action: {
      id: ACTION_ID, action_type: 'STOCK_SPLIT', action_type_display: 'Stock split', security_name: 'ABC Ltd',
      isin: ISIN, symbol: 'ABC', exchange: 'NSE', ex_date: EX_DATE, record_date: null,
      payment_date: null, old_units: '1', new_units: '2', cash_per_unit: null, source: 'NSE_CORPORATE_ACTIONS',
      source_reference: 'https://example.com', status: 'PENDING', notes: '', quantity_factor: null,
    },
    reconciliation: {
      id: RECONCILIATION_ID, status: 'PENDING', matched_transaction_ids: [], suggested_quantity: '150',
      identity_match_method: 'SOURCE_ISIN', candidate_asset_ids: [], candidate_assets: [], selected_asset_id: null,
      notes: '1 eligible position(s) can be accepted.', proposal_fingerprint: SHOWN_FINGERPRINT,
      can_accept: true, can_reject: true,
      summary: {
        status_counts: { PENDING: 1, ALREADY_RECORDED: 1 }, affected_portfolio_count: 2,
        affected_family_members: [MEMBER_PENDING, MEMBER_RECORDED], affected_asset_names: ['Direct Equity'],
        affected_asset_classes: ['Equity'], original_quantity: '100', proposed_adjustment: '100',
        resulting_quantity: '200', cash_amount: null, already_recorded: 1, manual_review: 0,
      },
      positions: [pendingPosition(), recordedPosition()],
    },
    exposures: [{ type: 'DIRECT', investment_name: 'ABC Ltd', investment_id: 5, holding_weight: null, snapshot_date: null }],
    reviewed: false, dismissed: false, direct_exposure: true,
    ...overrides,
  };
}

function page(results: unknown[]) {
  return of({ count: results.length, page: 1, page_size: 25, results });
}

describe('CorporateActionsComponent', () => {
  let fixture: ComponentFixture<CorporateActionsComponent>;
  let component: CorporateActionsComponent;
  let service: Record<string, ReturnType<typeof vi.fn>>;

  const text = () => (fixture.nativeElement as HTMLElement).textContent || '';
  const buttonLabels = () => Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button'))
    .map(button => button.textContent?.trim());
  const reloadWith = (results: unknown[]) => {
    service['getNotifications'].mockReturnValue(page(results));
    component.load();
    fixture.detectChanges();
  };

  beforeEach(async () => {
    service = {
      getNotifications: vi.fn(() => page([inboxItem()])),
      acceptReconciliation: vi.fn(() => of({ status: 'COMPLETED', created_transaction_ids: [CREATED_TRANSACTION_ID] })),
      rejectReconciliation: vi.fn(() => of({ status: 'REJECTED' })),
      refreshReconciliation: vi.fn(() => of({ status: 'PENDING' })),
      updateEventTerms: vi.fn(() => of({})),
      verifyEvent: vi.fn(() => of({})),
      updateNotification: vi.fn(() => of({})),
      decideReconciliation: vi.fn(() => of({})),
      decideIdentity: vi.fn(() => of({})),
    };
    await TestBed.configureTestingModule({
      imports: [CorporateActionsComponent],
      providers: [{ provide: CorporateActionsService, useValue: service }],
    }).compileComponents();
    fixture = TestBed.createComponent(CorporateActionsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => fixture.destroy());

  it('shows every position with its hierarchy, calculation and already-uploaded rows', () => {
    expect(text()).toContain('ABC Ltd');
    expect(text()).toContain(ISIN);
    expect(text()).toContain(`${MEMBER_PENDING}, ${MEMBER_RECORDED}`);
    expect(text()).toContain('100 shares × 2/1 (split) = 200.');
    expect(text()).toContain('Already recorded');
    expect(text()).toContain(`Existing #${UPLOADED_TRANSACTION_ID}`);
    expect(text()).toContain('To be booked');
    expect(text()).toContain('1 share(s) become 2 (1:2)');
  });

  it('accepts with the fingerprint that was shown and reloads the page', () => {
    const item = component.items[0];
    component.openAccept(item);
    component.confirmAccept(item);
    expect(service['acceptReconciliation']).toHaveBeenCalledWith(RECONCILIATION_ID, SHOWN_FINGERPRINT);
    expect(service['getNotifications']).toHaveBeenCalledTimes(2);
    expect(component.message).toContain('1 ledger transaction(s) created');
    expect(component.acceptingId).toBeNull();
  });

  it('keeps the refusal visible and reloads the refreshed proposal when the calculation changed', () => {
    service['acceptReconciliation'].mockReturnValue(
      throwError(() => ({ status: HTTP_CONFLICT, error: { detail: STALE_MESSAGE } })));
    component.confirmAccept(component.items[0]);
    expect(component.error).toBe(STALE_MESSAGE);
    expect(service['getNotifications']).toHaveBeenCalledTimes(2);
  });

  it('requires a reason before rejecting and never calls accept', () => {
    const item = component.items[0];
    component.openReject(item);
    component.rejectReason = ' ';
    component.confirmReject(item);
    expect(service['rejectReconciliation']).not.toHaveBeenCalled();
    component.rejectReason = 'Sold before the record date';
    component.confirmReject(item);
    expect(service['rejectReconciliation']).toHaveBeenCalledWith(RECONCILIATION_ID, 'Sold before the record date');
    expect(service['acceptReconciliation']).not.toHaveBeenCalled();
  });

  it('saves only changed event terms and skips the request when nothing changed', () => {
    const item = component.items[0];
    component.openTerms(item);
    component.saveTerms(item);
    expect(service['updateEventTerms']).not.toHaveBeenCalled();
    component.openTerms(item);
    component.termsForm['record_date'] = '2025-06-10';
    component.saveTerms(item);
    expect(service['updateEventTerms']).toHaveBeenCalledWith(ACTION_ID, { record_date: '2025-06-10' });
  });

  it('hides accept for indirect exposure', () => {
    reloadWith([inboxItem({
      direct_exposure: false,
      exposures: [{ type: 'MUTUAL_FUND', investment_name: 'Equity Fund', investment_id: 2, holding_weight: '4.5', snapshot_date: null }],
      reconciliation: { ...inboxItem().reconciliation, status: 'MANUAL_REVIEW', can_accept: false, positions: [] },
    })]);
    expect(buttonLabels()).not.toContain('Accept…');
    expect(text()).toContain('No direct stock transaction will be created');
  });

  it('shows the empty state when there are no notifications', () => {
    reloadWith([]);
    expect(component.items).toEqual([]);
    expect(component.total).toBe(0);
    expect(text()).toContain('No corporate action notifications');
    expect(buttonLabels()).not.toContain('Accept…');
  });

  it('drops rows without an event or reconciliation and tolerates missing lists', () => {
    const sparse = inboxItem({ exposures: undefined });
    (sparse.reconciliation as any).positions = undefined;
    (sparse.reconciliation as any).candidate_assets = undefined;
    (sparse.reconciliation as any).summary = undefined;
    reloadWith([null, { ...inboxItem(), reconciliation: null }, { ...inboxItem(), action: null }, sparse]);
    expect(component.items.length).toBe(1);
    expect(component.items[0].exposures).toEqual([]);
    expect(component.items[0].reconciliation.positions).toEqual([]);
    expect(component.items[0].reconciliation.candidate_assets).toEqual([]);
    expect(text()).toContain('ABC Ltd');
  });

  it('tolerates a position whose detail lists are missing', () => {
    const item = inboxItem();
    const position = pendingPosition() as any;
    delete position.matched_transactions;
    delete position.planned_entries;
    delete position.created_transaction_ids;
    (item.reconciliation as any).positions = [position];
    reloadWith([item]);
    const loaded = component.items[0].reconciliation.positions![0];
    expect(loaded.matched_transactions).toEqual([]);
    expect(loaded.planned_entries).toEqual([]);
    expect(loaded.created_transaction_ids).toEqual([]);
    expect(text()).toContain(MEMBER_PENDING);
  });

  it('reports a failed load instead of showing stale rows', () => {
    service['getNotifications'].mockReturnValue(throwError(() => ({ status: 500 })));
    component.load();
    fixture.detectChanges();
    expect(component.error).toContain('could not be loaded');
  });
});
