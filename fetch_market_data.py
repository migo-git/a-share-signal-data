#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv, json, math, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import List
import requests
try:
    import baostock as bs
except Exception:
    bs = None

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / 'data'
DATA_DIR.mkdir(exist_ok=True)
STOCKS = {
    '600309': {'name':'万华化学','market':'sh'},
    '688012': {'name':'中微公司','market':'sh'},
    '601899': {'name':'紫金矿业','market':'sh'},
    '600183': {'name':'生益科技','market':'sh'},
    '603259': {'name':'药明康德','market':'sh'},
    '601138': {'name':'工业富联','market':'sh'},
}
URL='https://push2his.eastmoney.com/api/qt/stock/kline/get'
FIELDS1='f1,f2,f3,f4,f5,f6'
FIELDS2='f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
HEADERS={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/plain,*/*','Referer':'https://quote.eastmoney.com/'}
FETCH_LIMIT=220; SAVE_LIMIT=180; MIN_VALID=120; TIMEOUT=20

def fnum(x):
    try:
        if x in (None,'','-','null'): return None
        return float(x)
    except Exception: return None

def inum(x):
    v=fnum(x); return None if v is None else int(round(v))

def clean(rows:List[dict])->List[dict]:
    by={}
    for r in rows:
        try: datetime.strptime(r['date'],'%Y-%m-%d')
        except Exception: continue
        vals=[r.get('open'),r.get('high'),r.get('low'),r.get('close')]
        if any(v is None or not math.isfinite(float(v)) or float(v)<=0 for v in vals): continue
        if r.get('volume') is None or r.get('amount') is None: continue
        if float(r['high']) < float(r['low']): continue
        by[r['date']]=r
    return [by[d] for d in sorted(by)]

def secid(symbol, market):
    return f"{'1' if market=='sh' else '0'}.{symbol}"

def fetch_eastmoney(symbol, market):
    params={'secid':secid(symbol,market),'fields1':FIELDS1,'fields2':FIELDS2,'klt':'101','fqt':'1','beg':'0','end':'20500101','lmt':str(FETCH_LIMIT)}
    err=None
    for attempt in range(3):
        try:
            r=requests.get(URL,params=params,headers=HEADERS,timeout=TIMEOUT)
            r.raise_for_status(); payload=r.json(); kl=(payload.get('data') or {}).get('klines') or []
            if not kl: raise RuntimeError('no klines')
            rows=[]
            for line in kl:
                p=line.split(',')
                if len(p)<11: continue
                row={'date':p[0],'open':fnum(p[1]),'close':fnum(p[2]),'high':fnum(p[3]),'low':fnum(p[4]),'volume':inum(p[5]),'amount':fnum(p[6]),'amplitude_pct':fnum(p[7]),'pct_change':fnum(p[8]),'change':fnum(p[9]),'turnover_pct':fnum(p[10])}
                rows.append(row)
            rows=clean(rows)
            if len(rows)<MIN_VALID: raise RuntimeError(f'only {len(rows)} valid bars')
            return rows[-SAVE_LIMIT:]
        except Exception as e:
            err=e; time.sleep(1.5*(attempt+1))
    raise RuntimeError(f'Eastmoney failed: {err}')

def fetch_baostock(symbol, market):
    if bs is None: raise RuntimeError('baostock package unavailable')
    lg=bs.login()
    if lg.error_code!='0': raise RuntimeError(f'BaoStock login failed: {lg.error_msg}')
    try:
        end=datetime.now().date(); start=end-timedelta(days=420)
        fields='date,code,open,high,low,close,volume,amount,adjustflag,turn,tradestatus,pctChg'
        rs=bs.query_history_k_data_plus(f'{market}.{symbol}',fields,start_date=start.isoformat(),end_date=end.isoformat(),frequency='d',adjustflag='2')
        if rs.error_code!='0': raise RuntimeError(f'BaoStock query failed: {rs.error_msg}')
        names=fields.split(','); rows=[]
        while rs.error_code=='0' and rs.next():
            d=dict(zip(names,rs.get_row_data()))
            if d.get('tradestatus')!='1': continue
            rows.append({'date':d['date'],'open':fnum(d['open']),'close':fnum(d['close']),'high':fnum(d['high']),'low':fnum(d['low']),'volume':inum(d['volume']),'amount':fnum(d['amount']),'turnover_pct':fnum(d['turn']),'pct_change':fnum(d['pctChg'])})
        rows=clean(rows)
        if len(rows)<MIN_VALID: raise RuntimeError(f'only {len(rows)} valid bars')
        return rows[-SAVE_LIMIT:]
    finally:
        try: bs.logout()
        except Exception: pass

def compare(a,b):
    pa={r['date']:r for r in a}; pb={r['date']:r for r in b}; common=sorted(set(pa)&set(pb))
    if len(common)<20: return {'ok':False,'reason':f'only {len(common)} overlapping dates'}
    la=a[-1]; lb=b[-1]
    if la['date']!=lb['date']: return {'ok':False,'reason':f"latest date mismatch: {la['date']} vs {lb['date']}"}
    latest_diff=abs(la['close']-lb['close'])/max(abs(la['close']),1e-9)
    if latest_diff>0.005: return {'ok':False,'reason':f'latest close mismatch {latest_diff:.2%}'}
    recent=common[-30:]; rel=[]; ratios=[]
    for d in recent:
        x=pa[d]['close']; y=pb[d]['close']
        rel.append(abs(x-y)/max(abs(x),1e-9)); ratios.append(x/y)
    md=median(rel); mx=max(rel); rm=median(ratios); spread=max(abs(r/rm-1) for r in ratios)
    ok=md<=0.01 and mx<=0.03 and spread<=0.02
    return {'ok':ok,'reason':'ok' if ok else f'qfq continuity mismatch median={md:.2%} max={mx:.2%} spread={spread:.2%}','common_dates':len(common),'latest_date':la['date'],'primary_close':la['close'],'secondary_close':lb['close'],'latest_close_rel_diff':latest_diff,'recent_median_rel_diff':md,'recent_max_rel_diff':mx,'recent_ratio_spread':spread}

def write_csv(symbol,rows):
    cols=['date','open','high','low','close','volume','amount','turnover_pct','pct_change','amplitude_pct','change']
    with (DATA_DIR/f'{symbol}.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore'); w.writeheader(); w.writerows(rows)

def now_cn(): return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')

def main():
    out={'schema_version':1,'generated_at_china':now_cn(),'adjustment':'qfq/forward-adjusted','required_valid_bars':MIN_VALID,'saved_bars_per_stock':SAVE_LIMIT,'stocks':{}}
    status={'generated_at_china':out['generated_at_china'],'all_valid_for_signal_axis':True,'stocks':{}}
    for symbol,meta in STOCKS.items():
        er=br=None; ee=be=None
        try: er=fetch_eastmoney(symbol,meta['market'])
        except Exception as e: ee=str(e)
        try: br=fetch_baostock(symbol,meta['market'])
        except Exception as e: be=str(e)
        chosen=er or br; cross=None; valid=False; reason=None
        if er and br:
            cross=compare(er,br); valid=bool(cross.get('ok')) and len(er)>=MIN_VALID
            if not valid: reason=cross.get('reason')
        elif chosen:
            reason='only one source available; cross-check requirement not met'
        else:
            reason='both sources failed'
        bars=(chosen or [])[-SAVE_LIMIT:]; latest=bars[-1] if bars else None
        if bars: write_csv(symbol,bars)
        rec={'symbol':symbol,'name':meta['name'],'market':meta['market'],'valid_for_signal_axis':valid,'reason_if_invalid':reason,'latest':latest,'bar_count':len(bars),'sources':{'eastmoney':{'ok':er is not None,'bar_count':len(er or []),'error':ee},'baostock':{'ok':br is not None,'bar_count':len(br or []),'error':be}},'cross_check':cross,'bars':bars}
        out['stocks'][symbol]=rec
        status['stocks'][symbol]={k:rec[k] for k in ['name','valid_for_signal_axis','reason_if_invalid','latest','bar_count','sources','cross_check']}
        if not valid: status['all_valid_for_signal_axis']=False
    (DATA_DIR/'latest.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    (DATA_DIR/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(status,ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__': sys.exit(main())
