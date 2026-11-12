import math, datetime as dt
exec(open('trend10.py').read().split('for s in')[0])
fx=dict(fetch('KRW=X'))
ser={s:dict(fetch(s)) for s in ['SPY','SCHD','QQQ','069500.KS']}
days=sorted(set(ser['SPY'])&set(ser['069500.KS']))
days=[d for d in days if d>=dt.date(2016,11,11)]
lastfx=None; krw={s:[] for s in ser}
for d in days:
    lastfx=fx.get(d,lastfx)
    for s in ser:
        krw[s].append(ser[s][d]*(1 if s.endswith('KS') else lastfx))
def run(w):
    v=1.0; hold={s:w[s]/krw[s][0] for s in w}; out=[1.0]; yr=days[0].year
    for i in range(1,len(days)):
        v=sum(hold[s]*krw[s][i] for s in w); out.append(v)
        if days[i].year!=yr:  # 연 1회 리밸런싱
            yr=days[i].year; hold={s:v*w[s]/krw[s][i] for s in w}
    yrs=(days[-1]-days[0]).days/365.25
    r=[math.log(out[i]/out[i-1]) for i in range(1,len(out))]; m=sum(r)/len(r)
    vol=math.sqrt(sum((x-m)**2 for x in r)/(len(r)-1)*252)
    pk=0;mdd=0;dd2=0
    for x in out:
        pk=max(pk,x);dd=x/pk-1;mdd=min(mdd,dd);dd2+=dd*dd
    t=list(range(len(out)));y=[math.log(x) for x in out];tm=sum(t)/len(t);ym=sum(y)/len(y)
    b=sum((a-tm)*(c-ym) for a,c in zip(t,y))/sum((a-tm)**2 for a in t)
    r2=1-sum((c-ym-b*(a-tm))**2 for a,c in zip(t,y))/sum((c-ym)**2 for c in y)
    return out[-1],out[-1]**(1/yrs)-1,vol,mdd,math.sqrt(dd2/len(out))*100,r2
P={'SPY100':{'SPY':1},'QQQ100':{'QQQ':1},'SCHD100':{'SCHD':1},'K200_100':{'069500.KS':1},
 'MINE 40/20/25/15':{'SPY':.40,'SCHD':.20,'QQQ':.25,'069500.KS':.15},
 'EQUAL 25x4':{'SPY':.25,'SCHD':.25,'QQQ':.25,'069500.KS':.25}}
for k,w in P.items():
    m,c,v,d,u,r2=run(w); print(f"{k:18s} x{m:.2f} CAGR {c*100:.1f}% vol {v*100:.1f}% MDD {d*100:.1f}% ulcer {u:.1f} R2 {r2:.3f}")
