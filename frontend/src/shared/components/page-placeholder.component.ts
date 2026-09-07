import { Component, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';

@Component({
  selector: 'app-page-placeholder',
  standalone: true,
  template: `
    <section class="placeholder-page">
      <div class="page-heading">
        <h1>{{ title }}</h1>
        <p>{{ description }}</p>
      </div>

      <div class="placeholder-card">
        <div class="placeholder-icon">W</div>

        <h2>{{ title }} module</h2>

        <p>This module will be implemented after the dashboard data layer is connected.</p>
      </div>
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .placeholder-page {
        max-width: 1440px;
        margin: 0 auto;
      }

      .page-heading {
        margin-bottom: 28px;
      }

      .page-heading h1 {
        margin: 0;
        font-size: 26px;
        line-height: 1.2;
        font-weight: 750;
        letter-spacing: -0.03em;
        color: #161b26;
      }

      .page-heading p {
        margin: 7px 0 0;
        font-size: 13px;
        color: #7d8492;
      }

      .placeholder-card {
        min-height: 360px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 40px;
        background: #ffffff;
        border: 1px solid #e7e9ef;
        border-radius: 14px;
        text-align: center;
      }

      .placeholder-icon {
        width: 52px;
        height: 52px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 14px;
        background: #111827;
        color: #ffffff;
        font-size: 20px;
        font-weight: 700;
      }

      .placeholder-card h2 {
        margin: 18px 0 0;
        font-size: 18px;
        color: #202530;
      }

      .placeholder-card p {
        max-width: 460px;
        margin: 8px 0 0;
        font-size: 13px;
        line-height: 1.6;
        color: #858c9a;
      }
    `,
  ],
})
export class PagePlaceholderComponent {
  private readonly route = inject(ActivatedRoute);

  get title(): string {
    return this.route.snapshot.data['title'] ?? 'PWMS';
  }

  get description(): string {
    return this.route.snapshot.data['description'] ?? '';
  }
}
