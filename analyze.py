#!/usr/bin/env python3
"""Nationwide fixed trifecta historical backtest. Data: community BoatraceOpenAPI v3.
Run: python analyze.py --start 2023-01-01 --end 2026-11-17
No third-party Python packages needed. Not a forward-looking EV estimate.
"""
import argparse
import concurrent.futures
import csv
import datetime as dt
import itertools
import json
import pathlib
import time
import urllib.error
import urllib.request
from collections import defaultdict

BASE = 'https://boatraceopenapi.github.io/results/v3/{year}/{ymd}.json'
PICKS = ['-'.join(map(str, x)) for x in itertools.permutations(range(1, 7), 3)]
HEADERS = {'User-Agent': 'JackpotHistoricalResearch/1.0 (personal analysis)'}


def dates(start, end):
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)


def fetch(day, directory):
    ymd = day.strftime('%Y%m%d')
    path = directory / f'{ymd}.json'
    if path.is_file():
        try:
            obj = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(obj.get('results'), list):
                return day, obj, None
        except (OSError, ValueError, AttributeError):
            pass
    url = BASE.format(year=day.year, ymd=ymd)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            obj = json.loads(data)
            if not isinstance(obj.get('results'), list):
                raise ValueError('missing results array')
            path.write_bytes(data)
            return day, obj, None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            error = str(exc)
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                break
            time.sleep(0.4 * (attempt + 1))
    return day, None, error


def accepted_race(r):
    """Conservative sample: all six boats classified 1..6, regular trifecta payout.
    Excludes cancellations, refunds, dead heats, disqualifications and unclear races.
    """
    try:
        boats = r['boats']
        places = [b.get('racer_place_number') for b in boats]
        if sorted(places) != list(range(1, 7)):
            return None
        pays = r['payouts']['trifecta']
        if len(pays) != 1:
            return None
        pick, amount = pays[0]['combination'], pays[0]['amount']
        if pick not in PICKS or not isinstance(amount, int) or amount <= 0:
            return None
        if int(r['stadium_number']) not in range(1, 25) or int(r['number']) not in range(1, 13):
            return None
        return pick, amount
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def main():
    parser = argparse.ArgumentParser(description='Jackpot Boat Race: 120 fixed trifectas backtest')
    parser.add_argument('--start', default='2023-01-01')
    parser.add_argument('--end', default='2026-11-17')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--out', default='jackpot_results')
    args = parser.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if start > end or end >= dt.date.today():
        parser.error('Date range must be ordered and end before today (avoid incomplete data).')
    out = pathlib.Path(args.out)
    raw = out / 'cache'
    raw.mkdir(parents=True, exist_ok=True)
    days = list(dates(start, end))
    print(f'Downloading or reading cache: {len(days)} days, {args.workers} workers')
    records, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, args.workers))) as pool:
        futures = {pool.submit(fetch, day, raw): day for day in days}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            day, obj, error = future.result()
            if error:
                failures.append((day.isoformat(), error))
            else:
                records.append((day, obj))
            if i % 100 == 0 or i == len(days):
                print(f'{i}/{len(days)} days processed, failures: {len(failures)}')

    records.sort(key=lambda t: t[0])
    year_totals = defaultdict(lambda: {'races': 0, 'hits': defaultdict(int), 'payouts': defaultdict(int)})
    eligible = excluded = total_seen = 0
    winning_events = []
    daily_counts = defaultdict(int)
    for day, obj in records:
        for r in sorted(obj['results'], key=lambda r: (r.get('stadium_number') or 0, r.get('number') or 0)):
            total_seen += 1
            p = accepted_race(r)
            if p is None:
                excluded += 1
                continue
            pick, amount = p
            eligible += 1
            daily_counts[day.isoformat()] += 1
            yr = year_totals[day.year]
            yr['races'] += 1
            yr['hits'][pick] += 1
            yr['payouts'][pick] += amount
            winning_events.append((day.isoformat(), r['stadium_number'], r['number'], pick, amount))

    def make_rows(yrs):
        n = sum(year_totals[y]['races'] for y in yrs)
        rows = []
        for pick in PICKS:
            hits = sum(year_totals[y]['hits'][pick] for y in yrs)
            paid = sum(year_totals[y]['payouts'][pick] for y in yrs)
            spent = n * 100
            rows.append({
                '買い目': pick, '購入対象レース数': n, '的中数': hits,
                '的中率(%)': round(100 * hits / n, 4) if n else 0,
                '払戻総額(円)': paid, '投資総額(円)': spent,
                '収支(円)': paid - spent,
                '回収率(%)': round(100 * paid / spent, 4) if spent else 0,
                '的中時平均払戻(円)': round(paid / hits) if hits else 0,
            })
        return sorted(rows, key=lambda r: (-r['回収率(%)'], r['買い目']))

    def write_csv(name, rows):
        if not rows:
            return
        with (out / name).open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    write_csv('overall_120.csv', make_rows(sorted(year_totals)))
    for year in sorted(year_totals):
        write_csv(f'year_{year}_120.csv', make_rows([year]))
    write_csv('winning_events.csv', [
        {'日付': d, '競艇場番号': s, 'レース番号': n, '買い目': p, '払戻(円)': a}
        for d, s, n, p, a in winning_events
    ])
    write_csv('daily_counts.csv', [{'日付':d,'対象レース数':n} for d,n in sorted(daily_counts.items())])
    (out / 'site_data.json').write_text(json.dumps({'days': [[d,n] for d,n in sorted(daily_counts.items())], 'events': [[d,int(s),int(n),p,a] for d,s,n,p,a in winning_events], 'failedDays': [d for d,_ in failures], 'start':start.isoformat(), 'end':end.isoformat(), 'excludedRaces':excluded, 'source':'BoatraceOpenAPI results v3 (unofficial)'}, ensure_ascii=False, separators=(',',':')),encoding='utf-8')
    write_csv('failed_days.csv', [{'日付': d, '理由': err} for d, err in sorted(failures)])
    summary = {
        '期間': f'{start} ~ {end}', '予定日数': len(days),
        '取得成功日数': len(records), '取得失敗日数': len(failures),
        '確認したレース数': total_seen, '集計対象レース数': eligible,
        '対象外レース数': excluded,
        '対象外の理由': '全6艇に1〜6着が付かない/3連単払戻が単一でない/データ不正',
        '注意': '返還を含む全レースの厳密な収支ではなく、通常完走6艇レースの比較。非公式データ。未来期待値ではない。',
        '年別対象レース数': {str(y): year_totals[y]['races'] for y in sorted(year_totals)},
    }
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('\nTOP 20 (historical return):')
    for i, row in enumerate(make_rows(sorted(year_totals))[:20], 1):
        print(f'{i:2}. {row["買い目"]:5s} {row["回収率(%)"]:8.2f}%  hits={row["的中数"]:5d}  profit={row["収支(円)"]:,} yen')
    print(f'\nSaved reports at: {out.resolve()}')
    if failures:
        print('WARNING: Missing dates; rerun to retry. Do not treat results as complete.')


if __name__ == '__main__':
    main()
