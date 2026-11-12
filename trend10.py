import json, urllib.request, datetime as dt, math
UA={"User-Agent":"Mozilla/5.0"}
def fetch(sym):
    p1=int(dt.datetime(2016,11,1).timestamp()); p2=int(dt.datetime(2026,11,13).timestamp())
    u=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d&events=div,split"
    r=json.load(urllib.request.urlopen(urllib.request.Request(u,headers=UA)))["chart"]["result"][0]
    ts=r["timestamp"]; adj=r["indicators"]["adjclose"][0]["adjclose"]
    return [(dt.date.fromtimestamp(t),a) for t,a in zip(ts,adj) if a]
def stats(s):
    s=[x for x in s if x[0]>=dt.date(2016,11,11)]
    d=[x[0] for x in s]; p=[x[1] for x in s]
    yrs=(d[-1]-d[0]).days/365.25
    cagr=(p[-1]/p[0])**(1/yrs)-1
    r=[math.log(p[i]/p[i-1]) for i in range(1,len(p))]
    m=sum(r)/len(r); vol=math.sqrt(sum((x-m)**2 for x in r)/(len(r)-1))*math.sqrt(252)
    peak=p[0]; mdd=0; dd2=[]; mdd_d=None; uw=0; maxuw=0; peakd=d[0]
    for di,x in zip(d,p):
        if x>=peak: peak=x; peakd=di
        dd=x/peak-1; dd2.append(dd*dd)
        if dd<mdd: mdd=dd; mdd_d=di
        maxuw=max(maxuw,(di-peakd).days)
    ulcer=math.sqrt(sum(dd2)/len(dd2))*100
    # R^2 of log price vs time
    t=[(x-d[0]).days for x in d]; y=[math.log(x) for x in p]
    tm=sum(t)/len(t); ym=sum(y)/len(y)
    b=sum((a-tm)*(c-ym) for a,c in zip(t,y))/sum((a-tm)**2 for a in t)
    ss=sum((c-ym)**2 for c in y); res=sum((c-(ym+b*(a-tm)))**2 for a,c in zip(t,y))
    r2=1-res/ss
    # yearly returns
    yr={}
    for di,x in zip(d,p): yr.setdefault(di.year,[x,x]); yr[di.year][1]=x
    years=sorted(yr); yret={}
    prev=None
    for Y in years:
        start=prev if prev else yr[Y][0]; yret[Y]=yr[Y][1]/start-1; prev=yr[Y][1]
    # rolling 12m positive ratio (monthly sample)
    idx=list(range(0,len(p),21)); pos=tot=0
    for i in idx:
        j=i+252
        if j<len(p): tot+=1; pos+= p[j]>p[i]
    return dict(cagr=cagr,vol=vol,mdd=mdd,mdd_d=mdd_d,maxuw=maxuw,ulcer=ulcer,r2=r2,sharpe=cagr/vol,calmar=cagr/abs(mdd),pos12=pos/tot,neg_years=[Y for Y in years[1:] if yret[Y]<0],yret=yret,start=d[0],end=d[-1],mult=p[-1]/p[0])
for s in ["SPY","SCHD","QQQ","069500.KS"]:
    x=stats(fetch(s))
    print(s, {k:(round(v,4) if isinstance(v,float) else v) for k,v in x.items() if k!='yret'})
    print("  yearly:", {k:f"{v*100:.1f}%" for k,v in x['yret'].items()})
