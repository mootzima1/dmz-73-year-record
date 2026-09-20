# -*- coding: utf-8 -*-
"""e영상역사관(대한뉴스) 클립 다운로더.
view 페이지 -> player iframe uuid -> proxy.jsp 스트림 XML -> 최고화질 m3u8 -> ffmpeg.
"""
import requests, re, subprocess, io, sys, os, json
S=requests.Session(); S.headers['User-Agent']='Mozilla/5.0'
S.headers['Referer']='https://www.ehistory.go.kr/'
OUT=sys.argv[1]

ITEMS=[  # (mediaid, mediadtl, 파일명슬러그)
 ("10348","22023","1350_88올림픽서울에"),
 ("10338","22711","1352_88올림픽서울개최확정"),
 ("11027","24300","1538_지하철4호선개통"),
 ("10783","23089","1549_지하철3호선개통"),
 ("10872","23507","1561_한강종합개발계획"),
 ("10872","23506","1561_올림픽자원봉사요원모집"),
 ("10873","23516","1563_지하철3_4호선전구간개통"),
 ("10946","23817","1573_86아시안게임"),
 ("10975","23977","1578_86서울아시아경기대회준비"),
 ("11017","24230","1582_손님맞이참여운동"),
 ("10976","23978","1591_올림픽대로개통"),
 ("10984","24050","1594_한강올림픽대로"),
 ("11065","24447","1607_한강개발"),
 ("11073","24475","1610_되살아난한강"),
 ("11079","24497","1613_86폐회식특집"),
 ("11265","25260","1642_주변미화정리"),
 ("12504","29214","1696_올림픽선수촌기자촌"),
 ("12523","29366","1714_평화의문완공"),
 ("12522","29357","1713_전국을밝힌평화의불"),
 ("12525","29377","1715_서울올림픽개막"),
 ("12527","29394","1717_올림픽폐회식"),
]

def page(mid, dtl):
    r=S.get('https://www.ehistory.go.kr/view/movie',
            params={'mediasrcgbn':'KV','mediaid':mid,'mediadtl':dtl,'mediagbn':'DH'})
    return r.text

def stream_url(mid, dtl):
    t=page(mid,dtl)
    m=re.search(r'/vodplayer/player\.jsp\?id=([0-9a-f-]+)&(?:amp;)?mediaid=([A-Z0-9_]+)', t)
    if not m: return None,None,t
    uuid, cid = m.group(1), m.group(2)
    x=S.get('https://www.ehistory.go.kr/vodplayer/proxy.jsp',
            params={f'http://hdvod.ktv.go.kr:8080/rest/file2/stream/{uuid};protocol=http;branchCd=;settId=;userId=web;contentId={cid}':''}).text
    if 'resultCode' not in x:
        x=S.get(f'https://www.ehistory.go.kr/vodplayer/proxy.jsp?http://hdvod.ktv.go.kr:8080/rest/file2/stream/{uuid};protocol=http;branchCd=;settId=;userId=web;contentId={cid}').text
    streams=re.findall(r'<stream><file>([^<]+)</file><label>([^<]+)</label></stream>', x)
    size=re.search(r'<videoSize>([^<]+)</videoSize>', x)
    dur=re.search(r'<playTime>(\d+)</playTime>', x)
    if not streams: return None,None,x
    # 최고 화질 우선
    order={'1080P':3,'720P':2,'360P':1}
    streams.sort(key=lambda s:-order.get(s[1],0))
    return streams[0][0], (size.group(1) if size else '?', dur.group(1) if dur else '?', streams[0][1]), None

os.makedirs(OUT, exist_ok=True)
log=[]
for mid,dtl,slug in ITEMS:
    dst=os.path.join(OUT, f'DH_{slug}.mp4')
    if os.path.exists(dst) and os.path.getsize(dst)>10000:
        print('skip', slug); continue
    try:
        u,info,err = stream_url(mid,dtl)
    except Exception as e:
        print('ERR', slug, e); continue
    if not u:
        print('NOSTREAM', slug); log.append((slug,'nostream')); continue
    p=subprocess.run(['ffmpeg','-v','error','-headers','Referer: https://www.ehistory.go.kr/\r\n',
                      '-i',u,'-c','copy','-bsf:a','aac_adtstoasc',dst,'-y'],
                     capture_output=True, encoding='utf-8', errors='replace')
    ok = os.path.exists(dst) and os.path.getsize(dst)>10000
    print(('OK  ' if ok else 'FAIL'), slug, info, (p.stderr or '')[:120])
    log.append((slug,'ok' if ok else 'fail', info))
io.open(os.path.join(OUT,'_dl_log.txt'),'w',encoding='utf-8').write('\n'.join(map(str,log)))
