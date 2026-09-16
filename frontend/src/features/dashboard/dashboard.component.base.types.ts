import './dashboard.component.base';

declare module './dashboard.component.base' {
  interface DashboardComponent {
    standardAllocations: Record<string, number>;
    standardAllocationDraft: Record<string, number>;
    standardAllocationEditing: boolean;
    standardAllocationSaving: boolean;
    standardAllocationError: string;

    startStandardAllocationEdit(): void;
    cancelStandardAllocationEdit(): void;
    updateStandardAllocation(category: string, rawValue: string): void;

    getStandardAllocation(category: string): number;
    getStandardAllocationDraftTotal(): number;
    getStandardAllocationTotalClass(): string;

    saveStandardAllocations(): void;

    getAllocationComment(group: {
      asset_category: string;
      percentage_of_total: number;
    }): string;

    getAllocationCommentClass(group: {
      asset_category: string;
      percentage_of_total: number;
    }): string;
  }
}
