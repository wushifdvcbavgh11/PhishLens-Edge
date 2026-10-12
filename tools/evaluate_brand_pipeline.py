"""Offline real-model replay of local browser captures (not an accuracy benchmark).

--evidence contains NAME.jpg and NAME-dom.json captured from the same viewport.
Derived cases measure state/policy robustness, not independent generalization.
"""
import argparse
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import socket
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--extra-only',action='store_true')
    args=parser.parse_args()
    attempts=[]
    def deny(*a,**k):
        attempts.append('network attempt')
        raise RuntimeError('Offline replay forbids network access')
    socket.socket.connect=deny
    socket.create_connection=deny
    socket.getaddrinfo=deny
    import app
    from PIL import Image, ImageDraw
    app._record_event=lambda *a:None
    outcomes=[]
    def run(name,raw,dom,url,expected,brand=None,mode='browser'):
        result=app.analyze(app.AnalyzeRequest(url=url,screenshot_base64=base64.b64encode(raw).decode(),
                                            dom_signals=dom,analysis_mode=mode))
        ok=result['decision']==expected and (brand is None or result['brand']==brand)
        ok=ok and result['analysis_status']==('completed' if mode=='browser' else 'degraded')
        outcomes.append({'case':name,'passed':ok,'expected':expected,'result':result})
        print(json.dumps({'case':name,'passed':ok,'decision':result['decision'],'brand':result['brand'],
              'seconds':result['total_time_s'],'status':result.get('visual_evidence',{}).get('status'),
              'candidate':result.get('brand_candidate'),'error':result.get('visual_error')},ensure_ascii=False),flush=True)
    brands=[] if args.extra_only else [('alipay','支付宝','alipay.com'),('wechat','微信','weixin.qq.com'),('icbc','工商银行','icbc.com.cn')]
    for bid,name,official in brands:
        raw=(args.evidence/f'{bid}.jpg').read_bytes()
        dom=json.loads((args.evidence/f'{bid}-dom.json').read_text(encoding='utf-8'))
        run(bid+'-local-login',raw,dom,f'http://127.0.0.1:8001/fake_{bid}.html','block',name)
        legit=deepcopy(dom);legit['page_domain']=official
        run(bid+'-official-domain',raw,legit,'https://'+official+'/','allow')
        unrelated=deepcopy(dom)
        for context in unrelated['credential_contexts']:
            context['text']='商家自己的会员账号登录'
        run(bid+'-unrelated-password',raw,unrelated,'http://127.0.0.1:8001/merchant','uncertain',name)
        control=deepcopy(dom)
        for region in control['logo_regions']:
            region['interactive']=True
        run(bid+'-third-party-control',raw,control,'http://127.0.0.1:8001/payment','uncertain')
        with Image.open(io.BytesIO(raw)) as image:
            scaled=image.resize((image.width*2,image.height*2))
            stream=io.BytesIO();scaled.save(stream,format='PNG')
            run(bid+'-2x-dpi',stream.getvalue(),dom,'http://127.0.0.1:8001/dpi','block',name)
            hidden=image.copy();draw=ImageDraw.Draw(hidden)
            for region in dom['logo_regions']:
                box=region['box']; sx=image.width/dom['viewport']['width'];sy=image.height/dom['viewport']['height']
                draw.rectangle((int(box[0]*sx)-3,int(box[1]*sy)-3,int(box[2]*sx)+3,int(box[3]*sy)+3),fill='white')
            stream=io.BytesIO();hidden.save(stream,format='PNG')
            run(bid+'-logo-occluded',stream.getvalue(),dom,'http://127.0.0.1:8001/occluded','uncertain')
        run(bid+'-screenshot-only',raw,None,'','uncertain',mode='screenshot_only')
    if not args.extra_only:
        dom=json.loads((args.evidence/'generic-dom.json').read_text(encoding='utf-8'))
        run('generic-login',(args.evidence/'generic.jpg').read_bytes(),dom,'http://127.0.0.1:8001/fake_generic_portal.html','uncertain')
    for name in ('payment','multiple'):
        if (args.evidence/(name+'.jpg')).is_file():
            dom=json.loads((args.evidence/(name+'-dom.json')).read_text(encoding='utf-8'))
            run(name+'-browser-negative',(args.evidence/(name+'.jpg')).read_bytes(),dom,
                'http://127.0.0.1:8002/'+name,'uncertain')
    report={'scope':'Local demos and derived policy cases; not independent real-world accuracy',
            'network_attempts':attempts,'passed':sum(item['passed'] for item in outcomes),
            'total':len(outcomes),'policy_version':app.POLICY_VERSION,'cases':outcomes}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False),flush=True)
    raise SystemExit(0 if all(r['passed'] for r in outcomes) and not attempts else 1)


if __name__=='__main__':
    main()
