import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface PortfolioNewsAlertListItem {
  id: number;
  holding_display_name: string;
  holding_type: 'EQUITY' | 'MUTUAL_FUND' | 'WATCHLIST';
  category: string;
  sentiment: 'positive' | 'negative' | 'neutral' | 'mixed';
  impact: 'very_low' | 'low' | 'moderate' | 'high' | 'critical';
  impact_score: number;
  materiality: 'trivial' | 'low' | 'moderate' | 'high' | 'critical';
  alert_score: number;
  notification_tier: 'critical' | 'high' | 'moderate' | 'low';
  article_title: string;
  article_source: string;
  article_published_at: string | null;
  source_quality: 'tier_1' | 'tier_2' | 'tier_3';
  source_count: number;
  is_read: boolean;
  notification_sent: boolean;
  created_at: string;
  source_type: 'NEWS' | 'EXCHANGE_FILING';
  filing_exchange?: string | null;
  filing_company?: string | null;
  filing_symbol?: string | null;
  filing_subject?: string | null;
  filing_event_type?: string | null;
  filing_severity?: string | null;
}

export interface NewsArticleSource {
  publisher_name: string;
  url: string;
  quality_tier: 'tier_1' | 'tier_2' | 'tier_3';
  published_at: string | null;
}

export interface PortfolioNewsAlertDetail extends PortfolioNewsAlertListItem {
  time_horizon: string;
  relevance_score: number;
  confidence: number;
  portfolio_weight_at_alert: number;
  summary: string;
  portfolio_implication: string;
  reason: string;
  key_facts: string;
  interpretation: string;
  uncertainty_notes: string;
  article_url: string;
  article_description: string;
  sources: NewsArticleSource[];
  filing_url?: string | null;
}

export interface PortfolioNewsListResponse {
  results: PortfolioNewsAlertListItem[];
  count: number;
}

export interface PortfolioNewsRawHolding {
  holding_type: 'EQUITY' | 'MUTUAL_FUND' | 'WATCHLIST';
  holding_id: number;
  holding_display_name: string;
}

export interface PortfolioNewsRawItem {
  id: number;
  title: string;
  url: string;
  source: string;
  description: string;
  published_at: string | null;
  source_quality: 'tier_1' | 'tier_2' | 'tier_3';
  source_count: number;
  matched_query: string;
  created_at: string;
  matched_holdings: PortfolioNewsRawHolding[];
}

export interface PortfolioNewsRawListResponse {
  results: PortfolioNewsRawItem[];
  count: number;
}


export interface PushConfigResponse {
  enabled: boolean;
  public_key: string;
}

export interface PortfolioNotificationsResponse {
  unread_count: number;
  results: PortfolioNewsAlertListItem[];
}

export interface PortfolioNewsDigestItem {
  alert_id: number;
  holding_display_name: string;
  holding_type: 'EQUITY' | 'MUTUAL_FUND';
  category: string;
  impact: string;
  materiality: string;
  sentiment: string;
  summary: string;
  alert_score: number;
  source_count: number;
}

export interface PortfolioNewsDigest {
  digest_date: string;
  item_count: number;
  items: PortfolioNewsDigestItem[];
}

@Injectable({
  providedIn: 'root',
})
export class NewsApiService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/ai`;

  // ======================================================
  // CSRF
  // ======================================================

  private readCsrfToken(): string | null {
    const cookies = document.cookie.split(';');

    for (const cookie of cookies) {
      const [name, ...valueParts] = cookie.trim().split('=');

      if (name === 'csrftoken') {
        return decodeURIComponent(valueParts.join('='));
      }
    }

    return null;
  }

  private postHeaders(): HttpHeaders | undefined {
    const csrfToken = this.readCsrfToken();

    return csrfToken
      ? new HttpHeaders({
          'X-CSRFToken': csrfToken,
          'Content-Type': 'application/json',
        })
      : undefined;
  }

  // ======================================================
  // NEWS FEED
  // ======================================================

  getNews(options?: {
    tier?: string;
    unreadOnly?: boolean;
    limit?: number;
    category?: string;
    sentiment?: string;
    holdingType?: string;
    holdingId?: number;
    dateRange?: string;
    sourceType?: string;
  }): Observable<PortfolioNewsListResponse> {
    let params = new HttpParams();

    if (options?.tier) {
      params = params.set('tier', options.tier);
    }

    if (options?.unreadOnly) {
      params = params.set('unread_only', 'true');
    }

    if (options?.limit) {
      params = params.set('limit', String(options.limit));
    }

    if (options?.category) {
      params = params.set('category', options.category);
    }

    if (options?.sentiment) {
      params = params.set('sentiment', options.sentiment);
    }

    if (options?.holdingType) {
      params = params.set('holding_type', options.holdingType);
    }

    if (options?.holdingId) {
      params = params.set('holding_id', String(options.holdingId));
    }

    if (options?.dateRange) {
      params = params.set('date_range', options.dateRange);
    }
    if (options?.sourceType) {
      params = params.set('source_type', options.sourceType);
    }

    return this.http.get<PortfolioNewsListResponse>(`${this.baseUrl}/news/`, {
      withCredentials: true,
      params,
    });
  }

  getRawNews(options?: {
    limit?: number;
    holdingType?: string;
    holdingId?: number;
    dateRange?: string;
  }): Observable<PortfolioNewsRawListResponse> {
    let params = new HttpParams();

    if (options?.limit) {
      params = params.set('limit', String(options.limit));
    }

    if (options?.holdingType) {
      params = params.set('holding_type', options.holdingType);
    }

    if (options?.holdingId) {
      params = params.set('holding_id', String(options.holdingId));
    }

    if (options?.dateRange) {
      params = params.set('date_range', options.dateRange);
    }

    return this.http.get<PortfolioNewsRawListResponse>(
      `${this.baseUrl}/news/raw/`,
      {
        withCredentials: true,
        params,
      },
    );
  }

  getNewsDetail(id: number): Observable<PortfolioNewsAlertDetail> {
    return this.http.get<PortfolioNewsAlertDetail>(`${this.baseUrl}/news/${id}/`, {
      withCredentials: true,
    });
  }

  getDigest(date?: string): Observable<PortfolioNewsDigest> {
    let params = new HttpParams();

    if (date) {
      params = params.set('date', date);
    }

    return this.http.get<PortfolioNewsDigest>(`${this.baseUrl}/news/digest/`, {
      withCredentials: true,
      params,
    });
  }

  // ======================================================
  // NOTIFICATIONS (BELL)
  // ======================================================

  getNotifications(limit?: number): Observable<PortfolioNotificationsResponse> {
    let params = new HttpParams();

    if (limit) {
      params = params.set('limit', String(limit));
    }

    return this.http.get<PortfolioNotificationsResponse>(`${this.baseUrl}/notifications/`, {
      withCredentials: true,
      params,
    });
  }

  markNotificationRead(id: number): Observable<{ id: number; is_read: boolean }> {
    return this.http.post<{ id: number; is_read: boolean }>(
      `${this.baseUrl}/notifications/${id}/read/`,
      {},
      {
        withCredentials: true,
        headers: this.postHeaders(),
      },
    );
  }

  markAllNotificationsRead(): Observable<{ updated: number }> {
    return this.http.post<{ updated: number }>(
      `${this.baseUrl}/notifications/read-all/`,
      {},
      {
        withCredentials: true,
        headers: this.postHeaders(),
      },
    );
  }

  getPushConfig(): Observable<PushConfigResponse> {
    return this.http.get<PushConfigResponse>(
      `${this.baseUrl}/notifications/push/config/`,
      {
        withCredentials: true,
      },
    );
  }

  savePushSubscription(subscription: PushSubscriptionJSON): Observable<{
    id: number;
    created: boolean;
    enabled: boolean;
  }> {
    return this.http.post<{
      id: number;
      created: boolean;
      enabled: boolean;
    }>(
      `${this.baseUrl}/notifications/push/subscribe/`,
      subscription,
      {
        withCredentials: true,
        headers: this.postHeaders(),
      },
    );
  }

  disablePushSubscription(endpoint: string): Observable<{ updated: number }> {
    return this.http.post<{ updated: number }>(
      `${this.baseUrl}/notifications/push/unsubscribe/`,
      { endpoint },
      {
        withCredentials: true,
        headers: this.postHeaders(),
      },
    );
  }
}
