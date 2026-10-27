"""Strict, source-preserving text-claim diagnostic. Never confirms visual identity."""
import re
from step6_text_context_variant import ALIASES,LOGIN,THIRD,NEGATED
MAX_NODES=128;MAX_CHARS=1024
def diagnose(dom):
    nodes=dom.get('nodes',[]);title=dom.get('page_title') or '';forms=dom.get('forms')
    reasons=[];mentions=[];claims=[];sources=[];contexts={}
    if not isinstance(nodes,list) or not isinstance(title,str):
        nodes=[];title='';reasons.append('INVALID_INPUT_SCHEMA')
    incomplete=len(nodes)>MAX_NODES or len(title)>MAX_CHARS
    if len(nodes)>MAX_NODES:reasons.append('NODE_LIMIT_EXCEEDED')
    if len(title)>MAX_CHARS:reasons.append('TITLE_LIMIT_EXCEEDED')
    formmap={};invalid_forms=False
    if not isinstance(forms,list):reasons.append('MISSING_EXPLICIT_FORM_SCOPE')
    else:
        for f in forms:
            if not isinstance(f,dict) or not isinstance(f.get('form_id'),str) or not f['form_id']:
                invalid_forms=True;reasons.append('INVALID_FORM_SCHEMA');continue
            if f['form_id'] in formmap:invalid_forms=True;reasons.append('DUPLICATE_FORM_ID')
            formmap[f['form_id']]=f
    for i,n in enumerate(nodes[:MAX_NODES]):
        if not isinstance(n,dict) or not isinstance(n.get('text',''),str):
            incomplete=True;reasons.append('INVALID_NODE_SCHEMA');continue
        text=n.get('text','');truncated=len(text)>MAX_CHARS
        if truncated:incomplete=True;reasons.append('TEXT_LIMIT_EXCEEDED')
        fid=n.get('form_id');nr=[]
        if invalid_forms:nr.append('INVALID_FORM_SCOPE')
        elif fid not in formmap:nr.append('MISSING_OR_UNKNOWN_FORM_ID')
        elif formmap[fid].get('has_password_input') is not True:nr.append('FORM_HAS_NO_PASSWORD_EVIDENCE')
        if n.get('within_credential_container') is not True:nr.append('OUTSIDE_CREDENTIAL_CONTAINER')
        role=n.get('role','unknown')
        if role!='heading':nr.append('ROLE_NOT_LOGIN_HEADING')
        if role in ('third_party_control','third_party_login','payment_badge') or n.get('third_party') is True:nr.append('THIRD_PARTY_ROLE')
        retained=text[:MAX_CHARS];brands=[b for b,p in ALIASES.items() if re.search(p,retained,re.I)]
        sources.append({'node_index':i,'role':role,'form_id':fid,'within_credential_container':n.get('within_credential_container'),'source':n.get('source'),'text':retained,'original_length':len(text),'truncated':truncated,'brands':brands,'reason_codes':nr})
        if fid in formmap and not invalid_forms:
            contexts.setdefault(fid,{'form_id':fid,'source_node_indices':[],'claim_node_indices':[],'brands':[],'reason_codes':[]})['source_node_indices'].append(i)
    # Negation is propagated only within an explicitly identified credential form.
    # Third-party controls and footers stay separate and cannot donate identity.
    negated_forms={s['form_id'] for s in sources if s['form_id'] in formmap and s['within_credential_container'] is True and s['role'] in ('heading','body','disclaimer','credential_text') and NEGATED.search(s['text'])}
    for s in sources:
        nr=s['reason_codes'];text=s['text'];fid=s['form_id']
        if incomplete:nr.append('INCOMPLETE_CONTEXT')
        if 'INVALID_INPUT_SCHEMA' in reasons:nr.append('INVALID_INPUT_SCHEMA')
        if NEGATED.search(text) or fid in negated_forms:nr.append('NEGATED_SAME_FORM_CONTEXT')
        if THIRD.search(text):nr.append('THIRD_PARTY_WORDING')
        if not LOGIN.search(text):nr.append('NO_LOGIN_PHRASE')
        if not s['brands']:nr.append('NO_SUPPORTED_BRAND_ALIAS')
        for brand in s['brands']:
            mention={'brand':brand,'node_index':s['node_index'],'role':s['role'],'form_id':fid,'text':text,'reason_codes':list(nr)}
            mentions.append(mention)
            if not nr:
                claim={**mention,'reason_codes':['UNVERIFIED_TEXT_CLAIM_EXPLICIT_FORM']};claims.append(claim)
                ctx=contexts[fid];ctx['claim_node_indices'].append(s['node_index']);ctx['brands'].append(brand)
    for ctx in contexts.values():
        ctx['brands']=sorted(set(ctx['brands']));ctx['claim_node_indices']=sorted(set(ctx['claim_node_indices']))
        if ctx['form_id'] in negated_forms:ctx['reason_codes'].append('NEGATED_SAME_FORM_CONTEXT')
    names=sorted({x['brand'] for x in claims})
    if len([c for c in contexts.values() if c['brands']])>1:reasons.append('MULTIPLE_FORM_CONTEXTS_NOT_FUSED')
    if incomplete:reasons.append('INCOMPLETE_CONTEXT')
    if not claims:reasons.append('NO_ELIGIBLE_LOGIN_CLAIM')
    return {'text_claims':claims,'text_mentions':mentions,'claim_status':'single_unverified_claim' if len(names)==1 else 'multiple_claims' if names else 'no_login_claim','source_nodes':sources,'page_title':{'role':'title','text':title[:MAX_CHARS],'truncated':len(title)>MAX_CHARS,'brand_mentions':[b for b,p in ALIASES.items() if re.search(p,title[:MAX_CHARS],re.I)],'reason_codes':['TITLE_CONTEXT_ONLY']},'form_contexts':list(contexts.values()),'reason_codes':sorted(set(reasons)),'omitted_nodes':max(0,len(nodes)-MAX_NODES),'visual_confirmed_brand':None,'risk_decision':'uncertain','production_enabled':False}
