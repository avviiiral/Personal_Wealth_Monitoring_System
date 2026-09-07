import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';

import { NewsApiService, PortfolioNewsAlertDetail } from '../../core/services/news-api.service';

@Component({
  selector: 'app-portfolio-news-detail',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './portfolio-news-detail.component.html',
  styleUrl: './portfolio-news-detail.component.scss',
})
export class PortfolioNewsDetailComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly newsApi = inject(NewsApiService);

  alert: PortfolioNewsAlertDetail | null = null;
  loading = true;
  error = '';

  ngOnInit(): void {
    const idParam = this.route.snapshot.paramMap.get('id');
    const id = idParam ? Number(idParam) : NaN;

    if (!id || Number.isNaN(id)) {
      this.error = 'This news item could not be found.';
      this.loading = false;
      return;
    }

    this.newsApi.getNewsDetail(id).subscribe({
      next: (alert) => {
        this.alert = alert;
        this.loading = false;
      },

      error: (error) => {
        console.error('Failed to load news detail:', error);
        this.error = 'This news item could not be found.';
        this.loading = false;
      },
    });
  }

  goBack(): void {
    this.router.navigate(['/portfolio-news']);
  }

  confidencePercent(): number {
    return this.alert ? Math.round(this.alert.confidence * 100) : 0;
  }

  formattedPublishedAt(): string {
    if (!this.alert?.article_published_at) {
      return 'Publication time unavailable';
    }

    return new Date(this.alert.article_published_at).toLocaleString();
  }
}
