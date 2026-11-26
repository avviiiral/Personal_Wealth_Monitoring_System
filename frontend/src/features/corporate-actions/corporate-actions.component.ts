import { CommonModule, DatePipe } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';
import {
  CorporateActionInboxItem,
  CorporateActionPosition,
  CorporateActionsService,
} from '../../app/core/services/corporate-actions.service';
import {
  POSITION_STATUS_LABELS,
  RECONCILIATION_STATUS_LABELS,
  apiErrorMessage,
  costAllocationLabel,
  describeTerms,
  eligiblePositions,
  formatAmount,
  formatQuantity,
  ineligiblePositions,
  isRestructuring,
  plannedEntryCount,
  resultingSecurityLabel,
  termsPatch,
} from './corporate-actions.utils';

type TermsForm = Record<string, string>;

const TERM_FIELDS = [
  'old_units', 'new_units', 'cash_per_unit', 'record_date', 'payment_date',
  'resulting_isin', 'resulting_symbol', 'resulting_security_name', 'cost_allocation_ratio', 'cash_in_lieu_price',
] as const;

@Component({
  selector:'app-corporate-actions', standalone:true, imports:[CommonModule,DatePipe,FormsModule],
  templateUrl:'./corporate-actions.component.html', styleUrl:'./corporate-actions.component.scss',
})
export class CorporateActionsComponent implements OnInit {
  private readonly service=inject(CorporateActionsService);
  private readonly changeDetector=inject(ChangeDetectorRef);
  readonly filters=[
    {label:'All actions',value:'ALL'},{label:'New',value:'NEW'},
    {label:'Pending review',value:'PENDING'},{label:'Identity approval',value:'IDENTITY'},
    {label:'Manual review',value:'MANUAL'},{label:'Already recorded',value:'RECORDED'},
    {label:'Completed',value:'COMPLETED'},{label:'Rejected',value:'REJECTED'},
    {label:'Cancelled',value:'CANCELLED'},
  ];
  readonly statusLabels=RECONCILIATION_STATUS_LABELS;
  readonly positionLabels=POSITION_STATUS_LABELS;
  readonly formatQuantity=formatQuantity;
  readonly formatAmount=formatAmount;
  readonly describeTerms=describeTerms;
  readonly resultingSecurityLabel=resultingSecurityLabel;
  readonly costAllocationLabel=costAllocationLabel;
  readonly isRestructuring=isRestructuring;
  readonly eligiblePositions=eligiblePositions;
  readonly ineligiblePositions=ineligiblePositions;
  readonly plannedEntryCount=plannedEntryCount;

  selectedFilter='ALL'; items:CorporateActionInboxItem[]=[]; total=0; page=1; pageSize=25;
  loading=false; error=''; message=''; busyId:number|null=null; selectedCandidates:Record<number,number>={};
  acceptingId:number|null=null; rejectingId:number|null=null; rejectReason='';
  termsId:number|null=null; termsForm:TermsForm={}; showIneligible:Record<number,boolean>={};

  ngOnInit():void{this.load();}
  load():void{
    this.loading=true;this.error='';
    this.changeDetector.markForCheck();
    this.service.getNotifications(this.selectedFilter,this.page,this.pageSize)
      .pipe(finalize(()=>{
        this.loading=false;
        // Explicitly schedule a view check after the HTTP stream completes.
        // This also keeps the loading state reliable in zoneless configurations.
        this.changeDetector.markForCheck();
      })).subscribe({
        next:r=>{
          // A row without its event or reconciliation cannot be reviewed safely.
          const rows=(Array.isArray(r?.results)?r.results:[])
            .filter((row:CorporateActionInboxItem|null)=>!!row?.action&&!!row?.reconciliation);
          for(const row of rows){
            row.exposures=Array.isArray(row.exposures)?row.exposures:[];
            row.reconciliation.positions=(Array.isArray(row.reconciliation.positions)?row.reconciliation.positions:[])
              .filter(Boolean).map(position=>({
                ...position,
                matched_transactions:Array.isArray(position.matched_transactions)?position.matched_transactions:[],
                planned_entries:Array.isArray(position.planned_entries)?position.planned_entries:[],
                created_transaction_ids:Array.isArray(position.created_transaction_ids)?position.created_transaction_ids:[],
              }));
            row.reconciliation.candidate_assets=Array.isArray(row.reconciliation.candidate_assets)?row.reconciliation.candidate_assets:[];
          }
          this.items=rows;
          this.total=Number(r?.count)||0;
          for(const item of this.items){
            const candidates=item.reconciliation?.candidate_assets||[];
            if(candidates.length && this.selectedCandidates[item.id]===undefined){
              this.selectedCandidates[item.id]=candidates[0].id;
            }
          }
          this.changeDetector.markForCheck();
        },
        error:()=>{
          this.error='Corporate action notifications could not be loaded. Please retry.';
          this.changeDetector.markForCheck();
        },
      });
  }
  selectFilter(value:string):void{this.selectedFilter=value;this.page=1;this.closePanels();this.load();}
  private closePanels():void{this.acceptingId=null;this.rejectingId=null;this.rejectReason='';this.termsId=null;}
  private done(message:string):void{this.message=message;this.closePanels();this.load();}
  private fail(error:any,fallback:string,reload=false):void{
    // load() clears the error banner when it starts, so reload first and then
    // show why the request was refused.
    if(reload)this.load();
    this.error=apiErrorMessage(error,fallback);
    this.changeDetector.markForCheck();
  }

  markReviewed(item:CorporateActionInboxItem):void{
    this.busyId=item.id;
    this.service.updateNotification(item.id,{reviewed:true}).pipe(finalize(()=>this.busyId=null))
      .subscribe({next:()=>{item.reviewed=true;this.changeDetector.markForCheck();},error:e=>this.fail(e,'Could not update notification.')});
  }
  verifyEvent(item:CorporateActionInboxItem):void{
    this.busyId=item.id;this.error='';this.message='';
    this.service.verifyEvent(item.action.id).pipe(finalize(()=>this.busyId=null)).subscribe({
      next:()=>this.done('Event terms marked as verified. No holding was changed.'),
      error:e=>this.fail(e,'Could not verify the event.'),
    });
  }

  // ---------------------------------------------------------------- accept
  openAccept(item:CorporateActionInboxItem):void{this.closePanels();this.acceptingId=item.id;this.error='';this.message='';}
  confirmAccept(item:CorporateActionInboxItem):void{
    const fingerprint=item.reconciliation.proposal_fingerprint||'';
    this.busyId=item.id;this.error='';
    this.service.acceptReconciliation(item.reconciliation.id,fingerprint).pipe(finalize(()=>this.busyId=null)).subscribe({
      next:r=>{
        const created=Array.isArray(r?.created_transaction_ids)?r.created_transaction_ids.length:0;
        this.done(`Accepted: ${created} ledger transaction(s) created. Holdings and portfolio positions were rebuilt.`);
      },
      // 409: the calculation changed; the refreshed proposal is reloaded for review.
      error:e=>{this.acceptingId=null;this.fail(e,'The corporate action could not be accepted.',e?.status===409);},
    });
  }

  // ---------------------------------------------------------------- reject
  openReject(item:CorporateActionInboxItem):void{this.closePanels();this.rejectingId=item.id;this.error='';this.message='';}
  confirmReject(item:CorporateActionInboxItem):void{
    const reason=this.rejectReason.trim();
    if(reason.length<3){this.error='Enter a short reason for the rejection.';return;}
    this.busyId=item.id;this.error='';
    this.service.rejectReconciliation(item.reconciliation.id,reason).pipe(finalize(()=>this.busyId=null)).subscribe({
      next:()=>this.done('Rejected. No transaction or holding was changed.'),
      error:e=>this.fail(e,'The rejection could not be saved.'),
    });
  }

  // ---------------------------------------------------------- recalculate
  refreshCalculation(item:CorporateActionInboxItem):void{
    this.busyId=item.id;this.error='';this.message='';
    this.service.refreshReconciliation(item.reconciliation.id).pipe(finalize(()=>this.busyId=null)).subscribe({
      next:()=>this.done('Calculation refreshed against the current transactions.'),
      error:e=>this.fail(e,'The calculation could not be refreshed.'),
    });
  }

  // ------------------------------------------------------------ event terms
  openTerms(item:CorporateActionInboxItem):void{
    this.closePanels();this.termsId=item.id;this.error='';this.message='';
    const action=item.action as unknown as Record<string,unknown>;
    this.termsForm=Object.fromEntries(TERM_FIELDS.map(field=>[field,action[field]==null?'':String(action[field])]));
  }
  saveTerms(item:CorporateActionInboxItem):void{
    const patch=termsPatch(item.action as unknown as Record<string,unknown>,this.termsForm);
    if(!Object.keys(patch).length){this.termsId=null;return;}
    this.busyId=item.id;this.error='';
    this.service.updateEventTerms(item.action.id,patch).pipe(finalize(()=>this.busyId=null)).subscribe({
      next:()=>this.done('Event terms saved and the calculation refreshed. Nothing has been booked.'),
      error:e=>this.fail(e,'The event terms could not be saved.'),
    });
  }

  decideIdentity(item:CorporateActionInboxItem,decision:'APPROVE'|'REJECT'):void{
    const assetId=this.selectedCandidates[item.id];
    if(decision==='APPROVE' && !item.reconciliation.candidate_assets?.some(x=>x.id===assetId)){
      this.error='Select one of the listed candidate holdings before approving.';return;
    }
    this.busyId=item.id;this.error='';this.message='';
    this.service.decideIdentity(item.reconciliation.id,decision,decision==='APPROVE'?assetId:undefined)
      .pipe(finalize(()=>this.busyId=null)).subscribe({
        next:()=>this.done(decision==='APPROVE'?'Identity approved and the entitlement booked.':'Identity match rejected. Nothing was changed.'),
        error:e=>this.fail(e,'Identity decision could not be saved.'),
      });
  }
  markAlreadyRecorded(item:CorporateActionInboxItem):void{
    this.busyId=item.id;this.service.decideReconciliation(item.reconciliation.id,'ALREADY_RECORDED')
      .pipe(finalize(()=>this.busyId=null)).subscribe({next:()=>this.done('Marked as already recorded.'),error:e=>this.fail(e,'Could not update reconciliation.')});
  }
  dismiss(item:CorporateActionInboxItem):void{
    if(item.reconciliation.status==='PENDING_USER_APPROVAL')return;
    this.busyId=item.id;this.service.updateNotification(item.id,{dismissed:true,reviewed:true})
      .pipe(finalize(()=>this.busyId=null)).subscribe({next:()=>this.done('Notification dismissed.'),error:e=>this.fail(e,'Could not dismiss notification.')});
  }

  isOpenStatus(item:CorporateActionInboxItem):boolean{
    return ['PENDING','MANUAL_REVIEW','ALREADY_RECORDED'].includes(item.reconciliation.status);
  }
  canEditTerms(item:CorporateActionInboxItem):boolean{
    return item.direct_exposure&&['PENDING','MANUAL_REVIEW'].includes(item.reconciliation.status);
  }
  hasCashInLieu(item:CorporateActionInboxItem):boolean{
    return (item.reconciliation.positions||[]).some(position=>position.cash_in_lieu_pending);
  }
  trackPosition(_:number,position:CorporateActionPosition):number{return position.id;}
  get pendingCount():number{return this.items.filter(x=>['PENDING','PENDING_USER_APPROVAL','MANUAL_REVIEW'].includes(x.reconciliation.status)).length;}
  get directCount():number{return this.items.filter(x=>x.direct_exposure).length;}
  get indirectCount():number{return this.items.filter(x=>!x.direct_exposure&&x.exposures.length>0).length;}
  min(value:number,total:number):number{return Math.min(value,total);}
  trackById(_:number,item:CorporateActionInboxItem):number{return item.id;}
}
