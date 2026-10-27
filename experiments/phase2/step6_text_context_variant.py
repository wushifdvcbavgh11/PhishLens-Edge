"""Independent diagnostic variant, not imported by production.
Bounds remain finite. Incomplete or ambiguous contexts cannot create claims.
No OCR, browser, visual confirmation, or risk policy is implemented here.
"""
import re
ALIASES={'AT&T':r'\bat&t\b|\bcurrently\.com\b','微信':r'微信|\bwechat\b','PayPal':r'\bpaypal\b'}
LOGIN=re.compile(r'sign\s*in|log\s*in|登录|登入|登录以',re.I)
THIRD=re.compile(r'with\s+(?:AT&T|WeChat|PayPal)|使用.*(?:登录|登入)|第三方|powered by',re.I)
NEGATED=re.compile(r'not affiliated|非官方|不代表|不要',re.I)
MAX_NODES=128;MAX_CHARS=1024
def diagnose(dom):
    nodes=dom.get('nodes',[]);title=dom.get('page_title') or ''
    incomplete=len(nodes)>MAX_NODES or len(title)>MAX_CHARS or any(len(n.get('text',''))>MAX_CHARS for n in nodes[:MAX_NODES])
    claims=[];excluded=[];contexts=[]
    forms=dom.get('forms');formmap={x['form_id']:x.get('has_password_input') for x in (forms or [])}
    formmap_valid=forms is None or len(formmap)==len(forms)
    ambiguous=False
    for idx,n in enumerate(nodes[:MAX_NODES]):
        text=n.get('text','')[:MAX_CHARS];fid=n.get('form_id')
        if forms is None:
            password=dom.get('has_password_input') is True
            scope='legacy_global_flag_unverified_form_scope'
        else:
            password=formmap_valid and fid in formmap and formmap[fid] is True
            scope='explicit_form_scope' if formmap_valid and fid in formmap else 'unresolved_form_scope'
            if n.get('within_credential_container') is True and scope=='unresolved_form_scope':ambiguous=True
        contexts.append({'node_index':idx,'form_scope':scope,'retained_chars':len(text),'truncated':len(n.get('text',''))>MAX_CHARS})
        for brand,pattern in ALIASES.items():
            if not re.search(pattern,text,re.I):continue
            eligible=(not incomplete and n.get('role')=='heading' and n.get('within_credential_container') is True and password and LOGIN.search(text) and not THIRD.search(text) and not NEGATED.search(text))
            (claims if eligible else excluded).append({'brand':brand,'text':text,'role':n.get('role'),'node_index':idx,'form_id':fid})
    # Title is retained separately as context, never elevated to a credential claim.
    title_mentions=[b for b,p in ALIASES.items() if re.search(p,title[:MAX_CHARS],re.I)]
    if ambiguous or not formmap_valid:
        excluded+=claims;claims=[]
    names=sorted({x['brand'] for x in claims})
    return {'text_claims':claims,'excluded_mentions':excluded,'claim_status':'single_unverified_claim' if len(names)==1 else 'multiple_claims' if names else 'no_login_claim','page_title_context':{'text':title[:MAX_CHARS],'mentions':title_mentions,'eligible_for_claim':False},'node_contexts':contexts,'context_status':'incomplete_context' if incomplete else 'ambiguous_form_scope' if ambiguous or not formmap_valid else 'within_structural_budget_not_semantically_verified','omitted_node_count':max(0,len(nodes)-MAX_NODES),'visual_confirmed_brand':None,'risk_decision':'uncertain','production_enabled':False}
