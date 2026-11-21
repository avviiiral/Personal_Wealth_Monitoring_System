import { CommonModule } from '@angular/common';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { environment } from '../../../environments/environment';

interface FundOption {
  scheme_code: string;
  scheme_name: string;
  amc_name: string;
  plan: string;
  option: string;
  level_1_branch: string;
  level_2_group: string;
  level_3_category: string;
}

interface CategoryFundsResponse {
  categories: string[];
  selected_category: string | null;
  fund_count: number;
  funds: FundOption[];
}

@Component({
  selector: 'app-mutual-fund-intelligence',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mutual-fund-intelligence.component.html',
  styleUrl: './mutual-fund-intelligence.component.scss',
})
export class MutualFundIntelligenceComponent implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly endpoint = `${environment.apiUrl}/api/mutual-fund-analytics/sebi/categories/`;

  categories: string[] = [];
  funds: FundOption[] = [];
  selectedCategory = '';
  selectedFundCode = '';
  selectedFund: FundOption | null = null;
  loadingCategories = false;
  loadingFunds = false;
  errorMessage = '';

  ngOnInit(): void {
    this.loadCategories();
  }

  loadCategories(): void {
    this.loadingCategories = true;
    this.errorMessage = '';

    this.http.get<CategoryFundsResponse>(this.endpoint, { withCredentials: true }).subscribe({
      next: (response) => {
        this.categories = response.categories ?? [];
        this.loadingCategories = false;
      },
      error: (error) => {
        console.error('Unable to load mutual fund categories:', error);
        this.errorMessage = error?.status === 401
          ? 'Your session has expired. Please sign in again.'
          : 'Unable to load categories. Please check the backend and try again.';
        this.loadingCategories = false;
      },
    });
  }

  onCategoryChange(category: string): void {
    this.selectedCategory = category;
    this.selectedFundCode = '';
    this.selectedFund = null;
    this.funds = [];
    this.errorMessage = '';

    if (!category) {
      return;
    }

    this.loadingFunds = true;
    const params = new HttpParams().set('category', category);

    this.http.get<CategoryFundsResponse>(this.endpoint, {
      params,
      withCredentials: true,
    }).subscribe({
      next: (response) => {
        this.funds = response.funds ?? [];
        this.loadingFunds = false;
      },
      error: (error) => {
        console.error('Unable to load funds for category:', error);
        this.errorMessage = 'Unable to load funds for this category. Please try again.';
        this.loadingFunds = false;
      },
    });
  }

  onFundChange(schemeCode: string): void {
    this.selectedFundCode = schemeCode;
    this.selectedFund = this.funds.find((fund) => fund.scheme_code === schemeCode) ?? null;
  }

  clearSelection(): void {
    this.selectedCategory = '';
    this.selectedFundCode = '';
    this.selectedFund = null;
    this.funds = [];
    this.errorMessage = '';
  }
}
