"""The opt-in plan-driven workflow CLI; legacy RAW conversion remains intact."""
import argparse
import json
from pathlib import Path
import sys
from .workflow import (prepare_batch, apply_plan, freeze_plan, run_batch, import_generated,
                       record_reviews, batch_status, stop_batch, resume_batch, cleanup_batch)
from .planning import export_requests, plan_with_command
from .workflow_report import build_report
from .delivery import package_batch
from .metrics import batch_metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description='分类与方案一次决策，本地执行，按风险复核')
    sub = parser.add_subparsers(dest='action',required=True)
    p = sub.add_parser('prepare',help='Create an isolated batch and classification previews')
    p.add_argument('input'); p.add_argument('--output',required=True)
    p.add_argument('--recursive',action='store_true');p.add_argument('--ids',nargs='+')
    p.add_argument('--preview-edge',type=int,default=1280)
    p.add_argument('--brief',help='Exact editing requirements for this batch; required for external planner calls')
    p.add_argument('--separate-pairs',action='store_true',help='Treat same-shot RAW and JPEG as separate photos')
    p.add_argument('--no-detect',action='store_true');p.add_argument('--raw-profile');p.add_argument('--rawtherapee-cli')
    for name in ('status','metrics','requests','run','report','freeze','stop','resume','package','cleanup'):
        p = sub.add_parser(name);p.add_argument('batch')
        if name=='requests':p.add_argument('--max-photos',type=int,default=8)
        if name in ('run','resume'):p.add_argument('--cv-runtime');p.add_argument('--model-dir');p.add_argument('--ids',nargs='+')
        if name=='package':p.add_argument('--output');p.add_argument('--gallery',action='store_true')
    p=sub.add_parser('plan');p.add_argument('batch')
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--response',help='Model plan JSON produced from the previews')
    group.add_argument('--command',help='Opt-in external vision adapter argv as JSON array; stdin request, stdout plan')
    p.add_argument('--max-photos',type=int,default=8);p.add_argument('--timeout',type=float,default=180)
    p.add_argument('--draft',action='store_true');p.add_argument('--revise',action='store_true')
    p=sub.add_parser('import-generated');p.add_argument('batch');p.add_argument('id');p.add_argument('image');p.add_argument('--metadata');p.add_argument('--replace',action='store_true');p.add_argument('--tile',choices=('TL','TR','BL','BR'))
    p.add_argument('--attempt-id',help='Reservation returned by generation-begin')
    p.add_argument('--reused',action='store_true',help='Import an existing result without a new generation request')
    p=sub.add_parser('generation-begin');p.add_argument('batch');p.add_argument('id');p.add_argument('--tile',choices=('TL','TR','BL','BR'));p.add_argument('--retry-reason')
    p=sub.add_parser('review');p.add_argument('batch');p.add_argument('--decisions',required=True,help='JSON list: id,final_revision,passed,notes; optional severity,issues,viewing_long_edge,reviewer')
    args=parser.parse_args(argv)
    try:
        if args.action=='prepare':
            m=prepare_batch(args.input,args.output,recursive=args.recursive,preview_edge=args.preview_edge,
                detect=not args.no_detect,raw_profile=args.raw_profile,raw_executable=args.rawtherapee_cli,selected_ids=args.ids,brief=args.brief,pair_raw_jpeg=not args.separate_pairs)
            result=batch_status(m['root'])
        else:
            root=str(Path(args.batch).expanduser().absolute())
            if args.action=='status':result=batch_status(root)
            elif args.action=='metrics':result=batch_metrics(root)
            elif args.action=='generation-begin':
                from .generation_budget import begin_generation
                result=begin_generation(root,args.id,tile=args.tile,retry_reason=args.retry_reason)
            elif args.action=='requests':result={'requests':export_requests(root,args.max_photos)}
            elif args.action=='plan':
                if args.response:
                    answer=json.loads(Path(args.response).read_text());apply_plan(root,answer.get('plan',answer),freeze=not args.draft,revise=args.revise)
                else:plan_with_command(root,json.loads(args.command),args.max_photos,args.timeout,freeze=not args.draft,revise=args.revise)
                result=batch_status(root)
            elif args.action in ('run','resume'):
                operation=run_batch if args.action=='run' else resume_batch
                operation(root,cv_runtime=args.cv_runtime,model_dir=args.model_dir,selected_ids=args.ids);result=batch_status(root)
            elif args.action=='freeze':
                freeze_plan(root);result=batch_status(root)
            elif args.action=='stop':
                stop_batch(root);result=batch_status(root)
            elif args.action=='cleanup':result=cleanup_batch(root)
            elif args.action=='import-generated':
                if args.attempt_id and args.reused:
                    raise ValueError('Choose either --attempt-id or --reused, not both')
                metadata=json.loads(Path(args.metadata).read_text()) if args.metadata else None
                if args.attempt_id or args.reused:
                    metadata=dict(metadata or {})
                    if args.attempt_id:metadata['attempt_id']=args.attempt_id
                    if args.reused:metadata['reused']=True
                import_generated(root,args.id,args.image,metadata,replace=args.replace,tile=args.tile);result=batch_status(root)
            elif args.action=='review':
                decisions=json.loads(Path(args.decisions).read_text())
                record_reviews(root,decisions)
                result=batch_status(root)
            elif args.action=='package':
                result=package_batch(root,args.output)
                if args.gallery:
                    try:result['gallery']=build_report(root)
                    except (ValueError,OSError,RuntimeError,KeyError) as exc:
                        result['gallery_error']=f'{type(exc).__name__}: {exc}'
            else:result={'gallery':build_report(root)}
        print(json.dumps(result,ensure_ascii=False))
        summary=result.get('summary',{})
        return 1 if summary.get('error') or summary.get('blocked') or result.get('gallery_error') else 0
    except KeyboardInterrupt:return 130
    except (ValueError,OSError,RuntimeError,KeyError) as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr)
        return 2
