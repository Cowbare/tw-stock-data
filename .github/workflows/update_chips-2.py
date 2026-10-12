import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STOCKS = {'2330':'台積電','2408':'南亞科','2303':'聯電','2891':'中信金'}
TZ = ZoneInfo('Asia/Taipei')
OUT = Path('chips')
OUT.mkdir(exist_ok=True)
HISTORY_DAYS = 120
LOOKBACK_CALENDAR_DAYS = 190
FIELDS = {
    'foreign_net_shares': '外陸資買賣超股數(不含外資自營商)',
    'trust_net_shares': '投信買賣超股數',
    'dealer_net_shares': '自營商買賣超股數',
    'dealer_self_net_shares': '自營商買賣超股數(自行買賣)',
    'dealer_hedge_net_shares': '自營商買賣超股數(避險)',
    'total_net_shares': '三大法人買賣超股數',
}

def fetch_day(day):
    params = urllib.parse.urlencode({'date':day.strftime('%Y%m%d'),'selectType':'ALL','response':'json'})
    url = 'https://www.twse.com.tw/rwd/zh/fund/T86?' + params
    request = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Accept':'application/json','Referer':'https://www.twse.com.tw/'})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get('stat') != 'OK':
        return None
    fields = result.get('fields', [])
    indices = {}
    for key, name in FIELDS.items():
        if name not in fields:
            raise ValueError(f'Missing TWSE field: {name}; actual={fields}')
        indices[key] = fields.index(name)
    if '證券代號' not in fields:
        raise ValueError('Missing stock ID field')
    id_index = fields.index('證券代號')
    found = {}
    for record in result.get('data', []):
        stock = str(record[id_index]).strip()
        if stock not in STOCKS:
            continue
        values = {}
        for key, idx in indices.items():
            raw = str(record[idx]).replace(',', '').replace('＋','+').replace('－','-').strip()
            values[key] = int(raw)
        if values['dealer_self_net_shares'] + values['dealer_hedge_net_shares'] != values['dealer_net_shares']:
            raise ValueError(f'{stock}: dealer sum mismatch')
        if values['foreign_net_shares'] + values['trust_net_shares'] + values['dealer_net_shares'] != values['total_net_shares']:
            raise ValueError(f'{stock}: institution sum mismatch')
        found[stock] = {'date':day.isoformat(), **values}
    return found if found else None

def load_existing(stock):
    file = OUT / f'{stock}.json'
    if not file.exists():
        return {}
    data = json.loads(file.read_text(encoding='utf-8'))
    return {r['date']: {k:v for k,v in r.items() if k == 'date' or k in FIELDS} for r in data.get('rows', [])}

def enrich(rows):
    for i, row in enumerate(rows):
        for n in (5,10,20,60):
            for field in ('foreign_net_shares','trust_net_shares','dealer_net_shares','total_net_shares'):
                row[f'{field}_sum{n}'] = sum(r[field] for r in rows[i-n+1:i+1]) if i+1 >= n else None

now = datetime.now(TZ)
today = now.date()
all_rows = {stock:load_existing(stock) for stock in STOCKS}
known_dates = set.intersection(*(set(d) for d in all_rows.values())) if all_rows else set()
success_count = 0
errors = []
for offset in range(LOOKBACK_CALENDAR_DAYS):
    day = today - timedelta(days=offset)
    if day.weekday() >= 5 or day.isoformat() in known_dates:
        continue
    result = None
    for attempt in range(3):
        try:
            result = fetch_day(day)
            break
        except Exception as exc:
            if attempt == 2:
                errors.append(f'{day}: {exc}')
            else:
                time.sleep(2*(attempt+1))
    if result:
        for stock, record in result.items():
            all_rows[stock][record['date']] = record
        success_count += 1
    time.sleep(0.4)

summary = {}
for stock, name in STOCKS.items():
    rows = [r for _,r in sorted(all_rows[stock].items()) if r['date'] <= today.isoformat()][-HISTORY_DAYS:]
    if not rows:
        raise RuntimeError(f'{stock}: no valid chip data; errors={errors[:5]}')
    enrich(rows)
    payload = {'stock':stock,'name':name,'source':'TWSE T86','unit':'shares','generated_at':datetime.now(TZ).isoformat(),'latest_date':rows[-1]['date'],'count':len(rows),'rows':rows}
    (OUT / f'{stock}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary[stock] = {'name':name,'latest_date':rows[-1]['date'],'count':len(rows),'foreign_net_shares':rows[-1]['foreign_net_shares'],'trust_net_shares':rows[-1]['trust_net_shares'],'dealer_net_shares':rows[-1]['dealer_net_shares']}
(OUT / 'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'summary':summary,'new_days':success_count,'errors':errors[:10]},ensure_ascii=False,indent=2))
