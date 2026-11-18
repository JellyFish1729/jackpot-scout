#!/usr/bin/env python3
"""Incremental data publisher. Standard Python only.
Initial: python update_data.py --backfill-years 3
Daily: python update_data.py (refreshes previous 10 JST days)
"""
import argparse
import concurrent.futures
import datetime as dt
import json
import pathlib
import sys
from zoneinfo import ZoneInfo
from analyze import fetch, accepted_race, dates

FILE = pathlib.Path('site_data.json')

def load_previous():
    if FILE.exists():
        data = json.loads(FILE.read_text(encoding='utf-8'))
        if not isinstance(data.get('days'),list) or not isinstance(data.get('events'),list):
            raise ValueError('Existing site_data.json is invalid')
        return data
    return {'days': [], 'events': [], 'failedDays': []}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill-years', type=int, default=0, help='Initial historical import; 1 to 10')
    ap.add_argument('--refresh-days', type=int, default=10)
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args()
    if not 0 <= args.backfill_years <= 10 or not 1 <= args.refresh_days <= 31:
        ap.error('Invalid year/day range')
    today = dt.datetime.now(ZoneInfo('Asia/Tokyo')).date()
    end = today - dt.timedelta(days=1)
    previous = load_previous()
    if args.backfill_years:
        start = end.replace(year=end.year-args.backfill_years) if not (end.month == 2 and end.day == 29) else end.replace(year=end.year-args.backfill_years, day=28)
    elif previous['days']:
        start = end-dt.timedelta(days=args.refresh_days-1)
    else:
        start = end-dt.timedelta(days=364)
        print('No existing data: first run bootstraps 1 year. Use --backfill-years 3 for longer history.')
    if start > end: return
    targets = list(dates(start,end))
    print(f'Fetching {len(targets)} days ({start}..{end})')
    raw = pathlib.Path('.local_cache')
    raw.mkdir(exist_ok=True)
    retrieved, failed = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,6))) as pool:
        for day,obj,error in pool.map(lambda day: fetch(day, raw),targets):
            if error:
                failed.append(day.isoformat())
            else:
                retrieved[day.isoformat()] = obj
    print(f'Obtained {len(retrieved)} days, failed {len(failed)}')
    # Only overwrite dates fetched successfully; preserve previously good data on API failures.
    replaced = set(retrieved)
    day_counts = {d:int(n) for d,n in previous['days'] if d not in replaced}
    events = [e for e in previous['events'] if e[0] not in replaced]
    excluded = 0
    for date,obj in sorted(retrieved.items()):
        count = 0
        seen_keys = set()
        for r in obj['results']:
            p = accepted_race(r)
            if p is None:
                excluded += 1
                continue
            pick, amount = p
            venue,race=int(r['stadium_number']),int(r['number'])
            key=(venue,race)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            count+=1
            events.append([date,venue,race,pick,amount])
        day_counts[date] = count
    # Track missing dates from current fetch, excluding dates subsequently fetched successfully.
    known = set(previous.get('failedDays',[])) - replaced
    known.update(failed)
    # Do not claim completeness if missing days remain within published date range.
    days_sorted = [[d,n] for d,n in sorted(day_counts.items())]
    events.sort(key=lambda e:(e[0],e[1],e[2]))
    result = {
        'start':days_sorted[0][0] if days_sorted else None,
        'end':days_sorted[-1][0] if days_sorted else None,
        'days':days_sorted, 'events':events,
        'failedDays':sorted(d for d in known if days_sorted and days_sorted[0][0] <= d <= days_sorted[-1][0]),
        'source':'BoatraceOpenAPI results v3 (unofficial; deprecated API)',
        'scope':'Only races with six classified finishers and one trifecta payout; refunded/nonstandard races excluded',
        'updatedAtJST':dt.datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds'),
    }
    tmp=FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    tmp.replace(FILE)
    print(f"Updated {FILE}: {len(days_sorted)} dates; {len(events)} outcomes; {len(result['failedDays'])} failed dates")
    # Fail workflow only when entirely unable to fetch any new data and no prior data exists.
    if not retrieved and not previous['days']:
        sys.exit('Initial fetch failed; no data published')

if __name__=='__main__':
    main()
