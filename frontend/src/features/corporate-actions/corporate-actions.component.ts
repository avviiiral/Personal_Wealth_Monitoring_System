import { CommonModule, DatePipe, DecimalPipe } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import { CorporateActionInboxItem, CorporateActionsService } from '../../app/core/services/corporate-actions.service';

@Component({
  selector: 'app-corporate-actions',
  standalone: true,
  imports: [CommonModule, FormsModule, DatePipe, DecimalPipe],
  templateUrl: './corporate-actions.component.html',
  styleUrl: './corporate-actions.component.scss',
})
export class CorporateActionsComponent implements OnInit {
  private readonly service = inject(CorporateActionsService);
  readonly filters = [
    { label: 'All actions', value: 'ALL' },
    { label: 'New', value: 'NEW' },
    { label: 'Pending review', value: 'PENDING' },
    { label: 'Already recorded', value: 'RECORDED' },
    { label: 'Completed', value: 'COMPLETED' },
    { label: 'Cancelled', value: 'CANCELLED' },
  ];
  selectedFilter = 'ALL';
  items: CorporateActionInboxItem[] = [];
  total = 0;
  page = 1;
  pageSize = 25;
  loading = false;
  error = '';
  busyId: number | null = null;

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading = true;
    this.error = '';
    this.service.getNotifications(this.selectedFilter, this.page, this.pageSize)
      .pipe(finalize(() => this.loading = false))
      .subscribe({
        next: (response) => { this.items = response.results || []; this.total = response.count || 0; },
        error: () => { this.error = 'Corporate action notifications could not be loaded. Please retry.'; },
      });
  }

  selectFilter(value: string): void { this.selectedFilter = value; this.page = 1; this.load(); }

  markReviewed(item: CorporateActionInboxItem): void {
    this.busyId = item.id;
    this.service.updateNotification(item.id, { reviewed: true })
      .pipe(finalize(() => this.busyId = null))
      .subscribe({ next: () => { item.reviewed = true; }, error: () => this.error = 'Could not update notification.' });
  }

  markAlreadyRecorded(item: CorporateActionInboxItem): void {
    this.busyId = item.id;
    this.service.decideReconciliation(item.reconciliation.id, 'ALREADY_RECORDED')
      .pipe(finalize(() => this.busyId = null))
      .subscribe({ next: () => this.load(), error: () => this.error = 'Could not update reconciliation.' });
  }

  dismiss(item: CorporateActionInboxItem): void {
    this.busyId = item.id;
    this.service.updateNotification(item.id, { dismissed: true, reviewed: true })
      .pipe(finalize(() => this.busyId = null))
      .subscribe({ next: () => this.load(), error: () => this.error = 'Could not dismiss notification.' });
  }

  get pendingCount(): number {
    return this.items.filter(item => item.reconciliation.status === 'PENDING').length;
  }

  get directCount(): number {
    return this.items.filter(item => item.direct_exposure).length;
  }

  get indirectCount(): number {
    return this.items.filter(item => !item.direct_exposure && item.exposures.length > 0).length;
  }

  trackById(_: number, item: CorporateActionInboxItem): number { return item.id; }
}
