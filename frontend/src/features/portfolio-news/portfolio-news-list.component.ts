import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';

import {
  NewsApiService,
  PortfolioNewsAlertListItem,
  PortfolioNewsDigest,
} from '../../core/services/news-api.service';

type TierFilter = 'all' | 'critical' | 'high' | 'moderate' | 'low';

type SentimentFilter = 'all' | 'positive' | 'negative' | 'neutral' | 'mixed';

type DateRangeFilter = 'all' | 'today' | '3d' | '7d' | '30d';

type ViewMode = 'feed' | 'digest';

@Component({
  selector: 'app-portfolio-news-list',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './portfolio-news-list.component.html',
  styleUrl: './portfolio-news-list.component.scss',
})
export class PortfolioNewsListComponent implements OnInit {
  private readonly newsApi = inject(NewsApiService);
  private readonly router = inject(Router);

  items: PortfolioNewsAlertListItem[] = [];
  loading = true;
  error = '';

  activeTier: TierFilter = 'all';
  activeSentiment: SentimentFilter = 'all';
  activeDateRange: DateRangeFilter = 'all';

  viewMode: ViewMode = 'feed';

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

  readonly dateRanges: { value: DateRangeFilter; label: string }[] = [
    { value: 'all', label: 'All time' },
    { value: 'today', label: 'Today' },
    { value: '3d', label: '3 days' },
    { value: '7d', label: '7 days' },
    { value: '30d', label: '30 days' },
  ];

  ngOnInit(): void {
    this.loadNews();
  }

  loadNews(): void {
    this.loading = true;
    this.error = '';

    this.newsApi
      .getNews({
        tier: this.activeTier === 'all' ? undefined : this.activeTier,
        sentiment: this.activeSentiment === 'all' ? undefined : this.activeSentiment,
        dateRange: this.activeDateRange === 'all' ? undefined : this.activeDateRange,
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

  selectDateRange(dateRange: DateRangeFilter): void {
    if (this.activeDateRange === dateRange) {
      return;
    }

    this.activeDateRange = dateRange;
    this.loadNews();
  }

  setViewMode(mode: ViewMode): void {
    if (this.viewMode === mode) {
      return;
    }

    this.viewMode = mode;

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
