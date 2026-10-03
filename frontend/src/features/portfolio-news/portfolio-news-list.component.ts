import { ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';

import {
  NewsApiService,
  PortfolioNewsAlertListItem,
  PortfolioNewsDigest,
  PortfolioNewsRawItem,
} from '../../core/services/news-api.service';

type TierFilter = 'all' | 'critical' | 'high' | 'moderate' | 'low';

type SentimentFilter = 'all' | 'positive' | 'negative' | 'neutral' | 'mixed';

type DateRangeFilter = 'all' | 'today' | '3d' | '7d' | '30d';

type ViewMode = 'all' | 'feed' | 'digest';
type SourceFilter = 'all' | 'NEWS' | 'CORPORATE_FILING';

@Component({
  selector: 'app-portfolio-news-list',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './portfolio-news-list.component.html',
  styleUrl: './portfolio-news-list.component.scss',
})
export class PortfolioNewsListComponent implements OnInit, OnDestroy {
  private readonly newsApi = inject(NewsApiService);
  private readonly router = inject(Router);
  private readonly changeDetector = inject(ChangeDetectorRef);

  private readonly livePollIntervalMs = 10_000;
  private livePollTimer: ReturnType<typeof setInterval> | null = null;
  private livePollInFlight = false;

  items: PortfolioNewsAlertListItem[] = [];
  loading = true;
  error = '';

  rawItems: PortfolioNewsRawItem[] = [];
  rawLoading = true;
  rawError = '';

  activeTier: TierFilter = 'all';
  activeSentiment: SentimentFilter = 'all';
  activeDateRange: DateRangeFilter = 'all';
  activeSource: SourceFilter = 'all';

  viewMode: ViewMode = 'all';

  digest: PortfolioNewsDigest | null = null;
  digestLoading = false;
  digestError = '';

  readonly tiers: { value: TierFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'critical', label: 'Critical' },
    { value: 'high', label: 'High' },
    { value: 'moderate', label: 'Moderate' },
    { value: 'low', label: 'Low' },
  ];

  readonly sentiments: { value: SentimentFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'positive', label: 'Positive' },
    { value: 'negative', label: 'Negative' },
    { value: 'neutral', label: 'Neutral' },
  ];

  readonly sources: { value: SourceFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'NEWS', label: 'News' },
    { value: 'CORPORATE_FILING', label: 'Corporate Filings' },
  ];

  readonly dateRanges: { value: DateRangeFilter; label: string }[] = [
    { value: 'all', label: 'All time' },
    { value: 'today', label: 'Today' },
    { value: '3d', label: '3 days' },
    { value: '7d', label: '7 days' },
    { value: '30d', label: '30 days' },
  ];

  ngOnInit(): void {
    this.loadRawNews();
    this.startLivePolling();
  }

  ngOnDestroy(): void {
    if (this.livePollTimer !== null) {
      clearInterval(this.livePollTimer);
      this.livePollTimer = null;
    }
  }

  private startLivePolling(): void {
    this.livePollTimer = setInterval(() => {
      this.pollForNewNews();
    }, this.livePollIntervalMs);
  }

  private pollForNewNews(): void {
    if (this.livePollInFlight || this.viewMode === 'digest') {
      return;
    }

    this.livePollInFlight = true;

    if (this.viewMode === 'all') {
      const viewAtStart = this.viewMode;
      const dateAtStart = this.activeDateRange;
      this.newsApi
        .getRawNews({
          dateRange: dateAtStart === 'all' ? undefined : dateAtStart,
          limit: 25,
        })
        .subscribe({
          next: (response) => {
            if (this.viewMode === viewAtStart && this.activeDateRange === dateAtStart) {
              this.mergeRawNews(response.results);
            }
            this.livePollInFlight = false;
          },
          error: (error) => {
            console.warn('Live portfolio news update failed:', error);
            this.livePollInFlight = false;
          },
        });

      return;
    }

    const viewAtStart = this.viewMode;
    const tierAtStart = this.activeTier;
    const sentimentAtStart = this.activeSentiment;
    const sourceAtStart = this.activeSource;
    const dateAtStart = this.activeDateRange;
    this.newsApi
      .getNews({
        tier: tierAtStart === 'all' ? undefined : tierAtStart,
        sentiment: sentimentAtStart === 'all' ? undefined : sentimentAtStart,
        dateRange: dateAtStart === 'all' ? undefined : dateAtStart,
        sourceType: sourceAtStart === 'all' ? undefined : sourceAtStart,
        limit: 25,
      })
      .subscribe({
        next: (response) => {
          if (
            this.viewMode === viewAtStart &&
            this.activeTier === tierAtStart &&
            this.activeSentiment === sentimentAtStart &&
            this.activeSource === sourceAtStart &&
            this.activeDateRange === dateAtStart
          ) {
            this.mergeAlerts(response.results);
          }
          this.livePollInFlight = false;
        },
        error: (error) => {
          console.warn('Live portfolio alert update failed:', error);
          this.livePollInFlight = false;
        },
      });
  }

  private mergeRawNews(newItems: PortfolioNewsRawItem[]): void {
    if (!newItems.length) {
      return;
    }

    const byId = new Map<number, PortfolioNewsRawItem>();
    [...newItems, ...this.rawItems].forEach((item) => byId.set(item.id, item));

    this.rawItems = Array.from(byId.values())
      .sort((a, b) => {
        const aTime = a.published_at ? new Date(a.published_at).getTime() : 0;
        const bTime = b.published_at ? new Date(b.published_at).getTime() : 0;
        return (bTime - aTime) || (b.id - a.id);
      })
      .slice(0, 25);

    this.changeDetector.detectChanges();
  }

  private mergeAlerts(newItems: PortfolioNewsAlertListItem[]): void {
    if (!newItems.length) {
      return;
    }

    const byId = new Map<number, PortfolioNewsAlertListItem>();
    [...newItems, ...this.items].forEach((item) => byId.set(item.id, item));

    this.items = Array.from(byId.values())
      .sort((a, b) => (b.alert_score - a.alert_score) || (b.id - a.id))
      .slice(0, 100);

    this.changeDetector.detectChanges();
  }

  loadRawNews(): void {
    this.rawLoading = true;
    this.rawError = '';

    this.newsApi
      .getRawNews({
        dateRange: this.activeDateRange === 'all' ? undefined : this.activeDateRange,
        limit: 25,
      })
      .subscribe({
        next: (response) => {
          this.rawItems = response.results;
          this.rawLoading = false;
          this.changeDetector.detectChanges();
        },
        error: (error) => {
          console.error('Failed to load raw portfolio news:', error);
          this.rawError = 'Unable to load all portfolio news right now.';
          this.rawLoading = false;
          this.changeDetector.detectChanges();
        },
      });
  }

  loadNews(): void {
    this.loading = true;
    this.error = '';

    this.newsApi
      .getNews({
        tier: this.activeTier === 'all' ? undefined : this.activeTier,
        sentiment: this.activeSentiment === 'all' ? undefined : this.activeSentiment,
        dateRange: this.activeDateRange === 'all' ? undefined : this.activeDateRange,
        sourceType: this.activeSource === 'all' ? undefined : this.activeSource,
        limit: 100,
      })
      .subscribe({
        next: (response) => {
          this.items = response.results;
          this.loading = false;
        },

        error: (error) => {
          console.error('Failed to load portfolio news:', error);
          this.error = 'Unable to load your portfolio news right now.';
          this.loading = false;
        },
      });
  }

  selectTier(tier: TierFilter): void {
    if (this.activeTier === tier) {
      return;
    }

    this.activeTier = tier;
    this.loadNews();
  }

  selectSentiment(sentiment: SentimentFilter): void {
    if (this.activeSentiment === sentiment) {
      return;
    }

    this.activeSentiment = sentiment;
    this.loadNews();
  }

  selectSource(source: SourceFilter): void {
    if (this.activeSource === source) return;
    this.activeSource = source;
    this.loadNews();
  }

  selectDateRange(dateRange: DateRangeFilter): void {
    if (this.activeDateRange === dateRange) {
      return;
    }

    this.activeDateRange = dateRange;
    if (this.viewMode === 'all') {
      this.loadRawNews();
    } else {
      this.loadNews();
    }
  }

  setViewMode(mode: ViewMode): void {
    if (this.viewMode === mode) {
      return;
    }

    this.viewMode = mode;

    if (mode === 'all' && !this.rawItems.length && !this.rawLoading) {
      this.loadRawNews();
    }

    if (mode === 'feed' && !this.items.length && !this.loading) {
      this.loadNews();
    }

    if (mode === 'digest' && !this.digest && !this.digestLoading) {
      this.loadDigest();
    }
  }

  loadDigest(): void {
    this.digestLoading = true;
    this.digestError = '';

    this.newsApi.getDigest().subscribe({
      next: (digest) => {
        this.digest = digest;
        this.digestLoading = false;
      },

      error: (error) => {
        console.error('Failed to load portfolio news digest:', error);
        this.digestError = 'Unable to load today\u2019s digest right now.';
        this.digestLoading = false;
      },
    });
  }

  openItem(item: PortfolioNewsAlertListItem): void {
    this.router.navigate(['/portfolio-news', item.id]);
  }

  openRawArticle(item: PortfolioNewsRawItem): void {
    window.open(item.url, '_blank', 'noopener,noreferrer');
  }

  rawHoldingNames(item: PortfolioNewsRawItem): string {
    return item.matched_holdings
      .map((holding) => holding.holding_display_name)
      .join(', ');
  }

  rawSourceCountLabel(item: PortfolioNewsRawItem): string {
    if (item.source_count <= 1) {
      return '';
    }

    return `Reported by ${item.source_count} sources`;
  }

  openDigestItem(alertId: number): void {
    this.router.navigate(['/portfolio-news', alertId]);
  }

  impactDotClass(item: PortfolioNewsAlertListItem): string {
    return `impact-dot impact-dot--${item.notification_tier}`;
  }

  materialityBadgeClass(materiality: string): string {
    return `materiality-badge materiality-badge--${materiality}`;
  }

  sourceCountLabel(item: PortfolioNewsAlertListItem): string {
    if (item.source_count <= 1) {
      return '';
    }

    return `Reported by ${item.source_count} sources`;
  }

  timeAgo(isoDate: string | null): string {
    if (!isoDate) {
      return '';
    }

    const then = new Date(isoDate).getTime();
    const diffMs = Date.now() - then;
    const diffMinutes = Math.floor(diffMs / 60000);

    if (diffMinutes < 1) {
      return 'just now';
    }

    if (diffMinutes < 60) {
      return `${diffMinutes} minute${diffMinutes === 1 ? '' : 's'} ago`;
    }

    const diffHours = Math.floor(diffMinutes / 60);

    if (diffHours < 24) {
      return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`;
    }

    const diffDays = Math.floor(diffHours / 24);

    return `${diffDays} day${diffDays === 1 ? '' : 's'} ago`;
  }
}
