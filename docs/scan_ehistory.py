# -*- coding: utf-8 -*-
import requests, re, html, io, json
S=requests.Session(); S.headers['User-Agent']='Mozilla/5.0'
BASE='https://www.ktv.go.kr/totalSearch/ehistory'
KWS=['올림픽','아시안게임','한강','지하철','잠실','바덴바덴','성화','올림픽대로','서울올림픽']
def get(kw,page=1):
    r=S.get(BASE,params={'design':'new','collection':'ehistory','ehistoryStleSe':'DH','baseKeyword':kw,'pageIndex':page})
    for enc in ('utf-8','euc-kr','cp949'):
        try:
            t=r.content.decode(enc); 
            if '대한뉴스' in t or '검색' in t: return t
        except Exception: pass
    return r.content.decode('utf-8','replace')
def parse(t):
    out=[];seen=set()
    for m in re.finditer(r'mediaid=(\d+)&(?:amp;)?mediadtl=(\d+)&(?:amp;)?mediagbn=DH[^>]*>(.{0,500}?)</a>',t,re.S):
        ti=html.unescape(re.sub(r'<[^>]+>','',m.group(3))).strip()
        ti=re.sub(r'\s+',' ',ti)
        k=(m.group(1),m.group(2))
        if not ti or k in seen: continue
        seen.add(k); out.append((k[0],k[1],ti))
    return out
o=io.open('scan_out.txt','w',encoding='utf-8')
allr={}
for kw in KWS:
    t=get(kw)
    cnt=re.search(r'총\s*<[^>]*>?\s*([\d,]+)',t) or re.search(r'([\d,]+)\s*건',re.sub(r'<[^>]+>',' ',t))
    o.write('=== %s (%s)\n'%(kw,cnt.group(1) if cnt else '?'))
    for a,b,c in parse(t):
        o.write('  %s/%s  %s\n'%(a,b,c[:90])); allr[(a,b)]=c
o.write('\nTOTAL UNIQUE %d\n'%len(allr))
o.close()
json.dump({f'{a}/{b}':c for (a,b),c in allr.items()}, io.open('cands.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
