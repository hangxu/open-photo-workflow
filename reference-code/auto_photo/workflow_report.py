"""An explicitly generated static original/final gallery from one state snapshot."""
from pathlib import Path
import json
import os
import tempfile
from urllib.parse import quote

from .workflow import load_batch


LABELS = {
    'queued': '等待预处理', 'preparing_input': '正在准备机内基础图', 'developing_raw': '正在显影', 'awaiting_plan': '等待处理计划',
    'planned': '计划已就绪', 'awaiting_generation': '等待生成', 'generated_ready': '生成图已导入',
    'needs_review': '等待审阅', 'awaiting_style_review': '等待样片审阅', 'needs_revision': '已退回',
    'blocked': '暂未完成', 'error': '处理失败', 'completed': '审阅通过', 'auto_complete': '按同场景审阅通过',
}


RAW_PREVIEW_LABELS = {
    'paired_jpeg': '同拍 JPG',
    'embedded_jpeg': '相机内嵌预览（含机内处理）',
    'embedded_bitmap': '相机内嵌预览（含机内处理）',
    'raw_small_render': 'RAW 基础显影预览',
    'raw_half_size': 'RAW 基础显影预览',
}


def _asset(root, value):
    if not value:
        return None
    path = Path(value).expanduser()
    path = path if path.is_absolute() else root / path
    if not path.is_file():
        return None
    return quote(os.path.relpath(path, root), safe='/')


def _rows(batch, root):
    rows = []
    policy=(batch.get('plan') or {}).get('collection_strategy',{}).get('delivery',{})
    for item in batch['items']:
        original = (item.get('paired_jpeg') or item.get('preview_path')) if item.get('raw_deferred') else (item.get('input') or item.get('source'))
        if original and Path(original).suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
            original = item.get('preview_path')
        before = _asset(root, original)
        preview = _asset(root, item.get('preview_path')) or before
        # An explicit current pointer never silently falls back to an old stage.
        after = _asset(root, item.get('jpeg_path') or item.get('final_path'))
        review = item.get('review') or {}
        required_edge=policy.get('viewing_long_edge',0)
        if item.get('final_dimensions'):
            required_edge=min(required_edge,max(item['final_dimensions']))
        reviewed = bool(item.get('final_revision')) and review.get('final_revision') == item.get('final_revision')
        status = LABELS.get(item.get('status'), item.get('status', '等待处理'))
        if after and not reviewed:
            status = '当前版本等待审阅'
        elif after and review.get('passed') is False and reviewed:
            status = '当前版本已退回'
        elif after and review.get('passed') is True and reviewed:
            if review.get('severity')=='major' or (policy.get('tolerance')=='strict' and review.get('severity')=='minor'):
                status='当前交付标准下需重新审阅'
            elif required_edge>review.get('viewing_long_edge',required_edge):
                status='更大交付尺度需要审阅'
            elif review.get('severity')=='minor':
                status='通过（含可接受轻微瑕疵）'
        notes=review.get('notes','') if reviewed else ''
        if reviewed and review.get('issues'):
            notes+=' 可接受问题：'+'；'.join(review['issues']) if review.get('passed') else ' 问题：'+'；'.join(review['issues'])
        rows.append({'id': item['id'], 'status': status, 'before': before, 'preview': preview,
                     'after': after, 'final_revision': item.get('final_revision'),
                     'before_label':RAW_PREVIEW_LABELS.get(item.get('preview_kind'), '原片预览') if item.get('raw_deferred') else '原片',
                     'notes': notes, 'error': item.get('error')})
    return rows


def build_report(root):
    """Create only index.html, without watching, decoding images or sidecar files."""
    batch = load_batch(root)
    root = Path(batch['root'])
    data = {'batch_id': batch['batch_id'], 'items': _rows(batch, root),
            'title':'照片对照',
            'delivery':(batch.get('plan') or {}).get('collection_strategy',{}).get('delivery',{})}
    encoded = json.dumps(data, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    document = HTML.replace('__PHOTO_DATA__', encoded)
    target = root / 'index.html'
    fd, temporary = tempfile.mkstemp(prefix='.report-', suffix='.tmp', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(document)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(target)


HTML = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>照片对照</title>
<style>
*{box-sizing:border-box}body{font:16px/1.65 system-ui;background:#f6f5f1;color:#25312a;margin:0;padding:28px}main{max-width:1500px;margin:auto}h1{font-size:28px;margin:0}h2{font-size:18px;margin:0}p{margin:8px 0 18px}.note{font-size:14px;color:#68736b}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}.card{padding:18px;background:white;border:1px solid #dce1da;border-radius:12px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}figure{margin:0;min-width:0}.pair img,.empty{width:100%;height:280px;object-fit:contain;background:#e7eae4}.pair img{display:block}.empty{display:flex;align-items:center;justify-content:center;text-align:center;padding:16px;color:#69756b}figcaption{font-size:13px;color:#68736b;margin-top:6px}a{color:#2b654b}button{font:inherit;background:#f3f5f0;color:#233a2b;border:1px solid #b0c2b5;border-radius:6px;padding:6px 12px;cursor:pointer}button:disabled{opacity:.45;cursor:default}button:focus-visible,a:focus-visible{outline:3px solid #c78936;outline-offset:3px}button[aria-pressed=true]{background:#285f46;color:white}dialog{width:calc(100vw - 30px);height:calc(100dvh - 30px);max-width:none;max-height:none;padding:16px;background:#19231d;color:white;border:1px solid #7d9985;border-radius:12px}dialog::backdrop{background:#000a}.viewer{height:100%;display:flex;flex-direction:column}.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.viewport{flex:1;min-height:0;overflow:auto}#large{display:block;margin:auto;max-width:none}.caption{margin:8px 0 0;font-size:14px}body.viewing{overflow:hidden}@media(max-width:850px){.grid{grid-template-columns:1fr}}@media(max-width:500px){body{padding:14px}.pair img,.empty{height:220px}.card{padding:12px}dialog{padding:10px;width:100vw;height:100dvh;border:0;border-radius:0}}
</style></head><body><main><h1 id="title">照片对照</h1><p id="summary" class="note"></p><p class="note">左侧原片，右侧当前成片。点击图片放大，可切换前后对照或按 100% 查看细节。</p><div class="grid" id="grid"></div></main>
<dialog id="dialog" aria-labelledby="caption"><div class="viewer"><div class="bar"><button id="close" autofocus>关闭</button><button id="previous">上一张</button><button id="next">下一张</button><button id="before" aria-pressed="false">原片</button><button id="after" aria-pressed="false">当前成片</button><button id="fit">适应窗口</button><button id="actual">100%</button><button id="less" aria-label="缩小">−</button><button id="more" aria-label="放大">＋</button><a id="download" download>下载图片</a></div><div class="viewport" id="viewport" tabindex="0" aria-label="图片滚动查看区域"><img id="large" alt=""></div><p id="caption" class="caption"></p></div></dialog>
<script id="photo-data" type="application/json">__PHOTO_DATA__</script>
<script>
const data=JSON.parse(document.getElementById('photo-data').textContent),rows=data.items,$=id=>document.getElementById(id);
const dialog=$('dialog'),large=$('large'),viewport=$('viewport');let current=0,side='after',fit=true,zoom=1,opener=null;
function el(tag,text,cls){const node=document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node}
function sideLabel(row,s){return s==='before'?(row.before_label||'原片'):'当前成片'}
function scale(){if(!large.naturalWidth||!dialog.open)return;if(fit)zoom=Math.min(1,Math.max(1,viewport.clientWidth-16)/large.naturalWidth,Math.max(1,viewport.clientHeight-16)/large.naturalHeight);large.style.width=Math.round(large.naturalWidth*zoom)+'px';large.style.height=Math.round(large.naturalHeight*zoom)+'px'}
function show(){const row=rows[current];if(!row)return;if(!row[side])side=row.after?'after':'before';$('before').disabled=!row.before;$('after').disabled=!row.after;for(const s of ['before','after'])$(s).setAttribute('aria-pressed',String(s===side));$('previous').disabled=current===0;$('next').disabled=current===rows.length-1;const url=row[side];$('caption').textContent=row.id+' · '+sideLabel(row,side);large.alt=$('caption').textContent;$('download').hidden=!url;large.hidden=!url;if(!url){large.removeAttribute('src');$('caption').textContent+=' · 暂无可用图片';return}fit=true;large.style.width='';large.style.height='';viewport.scrollTop=0;viewport.scrollLeft=0;$('download').href=url;large.src=url}
function open(index,chosen,link){current=index;side=chosen;opener=link;document.body.classList.add('viewing');dialog.showModal();show()}
large.onload=()=>{large.hidden=false;scale()};large.onerror=()=>{large.hidden=true;$('caption').textContent+=' · 图片已不可用'};
$('title').textContent=data.title||'照片对照';$('summary').textContent=rows.length+' 张 · 当前成片 '+rows.filter(r=>r.after).length+' 张'+(data.delivery.viewing_long_edge?' · 审阅参考长边 '+data.delivery.viewing_long_edge+' 像素':'');
rows.forEach((row,index)=>{const card=el('article',null,'card');card.append(el('h2',row.id+' · '+row.status));const pair=el('div',null,'pair');for(const chosen of ['before','after']){const figure=el('figure'),label=sideLabel(row,chosen),url=row[chosen];if(url){const link=el('a');link.href=url;link.setAttribute('aria-label','放大 '+row.id+' '+label);const image=el('img');image.src=chosen==='before'?(row.preview||url):url;image.alt=row.id+' '+label;image.loading='lazy';link.append(image);link.onclick=event=>{if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();open(index,chosen,link)};figure.append(link)}else figure.append(el('div',chosen==='before'?'暂无原片预览':'等待成片','empty'));figure.append(el('figcaption',label));pair.append(figure)}card.append(pair);if(row.notes)card.append(el('p',row.notes,'note'));if(row.error)card.append(el('p',row.error,'note'));$('grid').append(card)});
$('close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{document.body.classList.remove('viewing');opener?.focus({preventScroll:true})});
function move(delta){if(current+delta<0||current+delta>=rows.length)return;current+=delta;show()}
$('previous').onclick=()=>move(-1);$('next').onclick=()=>move(1);for(const chosen of ['before','after'])$(chosen).onclick=()=>{side=chosen;show()};
$('fit').onclick=()=>{fit=true;scale()};$('actual').onclick=()=>{fit=false;zoom=1;scale()};$('more').onclick=()=>{fit=false;zoom=Math.min(8,zoom*1.4);scale()};$('less').onclick=()=>{fit=false;zoom=Math.max(.02,zoom/1.4);scale()};
dialog.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'){event.preventDefault();move(-1)}if(event.key==='ArrowRight'){event.preventDefault();move(1)}});window.addEventListener('resize',()=>{if(fit)scale()});
</script></body></html>'''
