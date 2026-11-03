import { CommonModule, DatePipe } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { finalize } from 'rxjs';
import { CorporateActionInboxItem, CorporateActionsService } from '../../app/core/services/corporate-actions.service';

@Component({
  selector:'app-corporate-actions', standalone:true, imports:[CommonModule,DatePipe],
  templateUrl:'./corporate-actions.component.html', styleUrl:'./corporate-actions.component.scss',
})
export class CorporateActionsComponent implements OnInit {
  private readonly service=inject(CorporateActionsService);
  private readonly changeDetector=inject(ChangeDetectorRef);
  readonly filters=[
    {label:'All actions',value:'ALL'},{label:'New',value:'NEW'},
    {label:'Pending review',value:'PENDING'},{label:'Identity approval',value:'IDENTITY'},
    {label:'Already recorded',value:'RECORDED'},{label:'Completed',value:'COMPLETED'},
    {label:'Cancelled',value:'CANCELLED'},
  ];
  selectedFilter='ALL'; items:CorporateActionInboxItem[]=[]; total=0; page=1; pageSize=25;
  loading=false; error=''; busyId:number|null=null; selectedCandidates:Record<number,number>={};

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
          const rows=Array.isArray(r?.results)?r.results:[];
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
  selectFilter(value:string):void{this.selectedFilter=value;this.page=1;this.load();}
  markReviewed(item:CorporateActionInboxItem):void{
    this.busyId=item.id;
    this.service.updateNotification(item.id,{reviewed:true}).pipe(finalize(()=>this.busyId=null))
      .subscribe({next:()=>item.reviewed=true,error:()=>this.error='Could not update notification.'});
  }
  confirmAction(item:CorporateActionInboxItem):void{
    if(item.reconciliation.status==='PENDING_USER_APPROVAL'||!item.direct_exposure||
      item.action.status!=='PENDING'||item.reconciliation.status!=='PENDING')return;
    this.busyId=item.id;
    this.service.confirmAction(item.action.id).pipe(finalize(()=>this.busyId=null))
      .subscribe({next:()=>this.load(),error:e=>this.error=e?.error?.detail||'Could not confirm corporate action.'});
  }
  decideIdentity(item:CorporateActionInboxItem,decision:'APPROVE'|'REJECT'):void{
    const assetId=this.selectedCandidates[item.id];
    if(decision==='APPROVE' && !item.reconciliation.candidate_assets?.some(x=>x.id===assetId)){
      this.error='Select one of the listed candidate holdings before approving.';return;
    }
    this.busyId=item.id;this.error='';
    this.service.decideIdentity(item.reconciliation.id,decision,decision==='APPROVE'?assetId:undefined)
      .pipe(finalize(()=>this.busyId=null)).subscribe({
        next:()=>this.load(),error:e=>this.error=e?.error?.detail||'Identity decision could not be saved.',
      });
  }
  markAlreadyRecorded(item:CorporateActionInboxItem):void{
    this.busyId=item.id;this.service.decideReconciliation(item.reconciliation.id,'ALREADY_RECORDED')
      .pipe(finalize(()=>this.busyId=null)).subscribe({next:()=>this.load(),error:()=>this.error='Could not update reconciliation.'});
  }
  dismiss(item:CorporateActionInboxItem):void{
    if(item.reconciliation.status==='PENDING_USER_APPROVAL')return;
    this.busyId=item.id;this.service.updateNotification(item.id,{dismissed:true,reviewed:true})
      .pipe(finalize(()=>this.busyId=null)).subscribe({next:()=>this.load(),error:()=>this.error='Could not dismiss notification.'});
  }
  get pendingCount():number{return this.items.filter(x=>['PENDING','PENDING_USER_APPROVAL'].includes(x.reconciliation.status)).length;}
  get directCount():number{return this.items.filter(x=>x.direct_exposure).length;}
  get indirectCount():number{return this.items.filter(x=>!x.direct_exposure&&x.exposures.length>0).length;}
  min(value:number,total:number):number{return Math.min(value,total);}
  trackById(_:number,item:CorporateActionInboxItem):number{return item.id;}
}
