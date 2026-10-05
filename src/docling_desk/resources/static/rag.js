'use strict';
window.RagBrowser=(()=>{
  const el=id=>document.getElementById(id), files={context:'rag.jsonl',search:'rag-index.jsonl',docling:'rag-docling.jsonl'};
  let job=null,policy=null,cache={},controller=null,visible=false;
  function setJob(next){controller?.abort();controller=null;job=next;policy=null;cache={};el('ragMode').value='context';el('ragCards').replaceChildren();el('ragStatus').textContent='';el('ragNotice').textContent='';}
  function hide(){visible=false;}
  function parent(id){el('ragMode').value='context';render('context');const card=Array.from(el('ragCards').children).find(e=>e.dataset.chunkId===id);card?.scrollIntoView({block:'start'});}
  function render(mode){
    const chunks=cache[mode]||[];el('ragCards').replaceChildren();
    const names={context:'文脈単位',search:'検索用の小分け',docling:'従来のDocling分割'};
    el('ragStatus').textContent=`${names[mode]} ${chunks.length} 件 · 親文脈 ${policy.context_chunks} 件 · 検索用 ${policy.search_chunks} 件 · 従来 ${policy.docling_chunks} 件`;
    const tolerance=policy.tolerance_chars||0,threshold=policy.split_threshold_chars??policy.target_chars+tolerance;
    el('ragNotice').textContent=mode==='context'?'本文・表を対象とし、画像・図の情報は含めません。表の管理用タグや元行番号は本文から除いています。PPTは1スライド、XLSXは1シートをまとめています。長いシートは文脈の保存用です。検索用の小分けから親IDをたどって参照します。PDFは従来の見出し分割を保持しています。':mode==='search'?`画像・図の情報は含めません。表は行の途中で切らず、列見出しを繰り返します。表IDや元行番号は本文に含めず、出典情報に保持します。目安は${policy.target_chars}文字です。${tolerance?`猶予は${tolerance}文字で、${threshold}文字までは分割しません。小さな末尾は${threshold}文字以内なら前の部分に統合します。シート名・列見出しも文字数に含めます。`:""}埋め込みモデルのトークン上限は未検証です。`:'比較用にDoclingの分割を表示しています。画像・図の情報を除外した出力です。標準の文脈単位より細かい分割です。';
    for(let i=0;i<chunks.length;i++){
      const c=chunks[i],card=document.createElement('article');card.className='card';card.dataset.chunkId=c.id;card.dataset.kind=c.kind||'docling';card.dataset.parentId=c.parent_id||'';
      const title=document.createElement('strong');title.textContent=`${c.unit||c.headings.join(' › ')||'チャンク '+(i+1)} · ${c.text.length.toLocaleString('ja')}文字${c.row_range?.length?' · 元行 '+c.row_range.join('–'):''}`;
      const body=document.createElement('p');body.textContent=c.text;card.append(title,body);
      if(c.oversize){const note=document.createElement('p');note.className='rag-size-note';note.textContent=mode==='search'?'行・段落などのまとまりが分割基準を超えています。内容を切り捨てず保持しています。':'文字数の目安を超える親文脈です。検索用の小分けを別に用意しています。';card.append(note);}
      if(c.parent_id){const button=document.createElement('button');button.type='button';button.textContent='親文脈を見る';button.addEventListener('click',()=>parent(c.parent_id));card.append(button);}
      const details=document.createElement('details'),summary=document.createElement('summary'),meta=document.createElement('pre');summary.textContent='出典・要素参照';meta.textContent=`ID: ${c.id}\n親ID: ${c.parent_id||'なし'}\nページ: ${c.pages.join(', ')||'位置未取得'}\n参照: ${c.refs.join(', ')}\n補助文脈の参照: ${(c.context_refs||[]).join(', ')}\n表とキャプション: ${JSON.stringify(c.relations||[])}\n出典: ${c.source}\nSHA256: ${c.source_sha256}`;details.append(summary,meta);card.append(details);el('ragCards').append(card);
    }
  }
  async function show(){
    visible=true;controller?.abort();controller=null;if(!job||!['success','partial'].includes(job.state))return;const mode=el('ragMode').value;if(cache[mode]&&policy){render(mode);return;}
    controller?.abort();controller=new AbortController();const request=controller,id=job.id;el('ragStatus').textContent='RAGの文脈データを読み込んでいます…';
    try{
      const names=['rag-policy.json',files[mode],...(mode==='search'&&!cache.context?['rag.jsonl']:[])];
      const responses=await Promise.all(names.map(name=>fetch(`/files/${id}/${name}`,{signal:request.signal})));
      if(responses.some(r=>!r.ok))throw new Error('RAG出力を読み込めませんでした。');
      const texts=await Promise.all(responses.map(r=>r.text()));if(job.id!==id||controller!==request)return;
      policy=JSON.parse(texts[0]);cache[mode]=texts[1].trim().split('\n').filter(Boolean).map(line=>JSON.parse(line));if(texts[2])cache.context=texts[2].trim().split('\n').filter(Boolean).map(line=>JSON.parse(line));if(visible)render(mode);
    }catch(error){if(error.name!=='AbortError'&&job.id===id)el('ragStatus').textContent=error.message;}finally{if(controller===request)controller=null;}
  }
  el('ragMode').addEventListener('change',show);
  return {setJob,show,hide};
})();
