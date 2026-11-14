#!/usr/bin/env python3
"""BearShare Australia: buy signals for ASX shares and ETFs.

The "BearShare signals" GitHub Action (.github/workflows/bearshare-signals.yml)
runs this after the ASX closes each weekday. For every entry in
bearshare/watchlist.json it

  1. downloads about five years of daily prices and dividends (Yahoo Finance),
  2. downloads the last 30 days of headlines (Google News, Australian edition),
  3. scores five things out of 100: trend, momentum, long-term track record,
     entry timing and news sentiment,
  4. turns the score into Strong Buy / Buy / Watch / Avoid and writes out, in
     plain English, why,

and saves everything to data/bearshare.json, which bearshare/index.html reads.

Standard library only, so the workflow has nothing to install.
Run it yourself with:  python3 bearshare/build_signals.py [--only BHP,VAS]
"""

import argparse
import bisect
import json
import math
import os
import random
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCHLIST = os.path.join(ROOT, 'bearshare', 'watchlist.json')
OUT = os.path.join(ROOT, 'data', 'bearshare.json')

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
PAUSE = 0.8        # seconds between requests, to stay polite
NEWS_DAYS = 30
HISTORY_YEARS = 5.2

# How much each part counts towards the overall score. History (momentum +
# track record) is 35%, news is 25%.
WEIGHTS = {'trend': 0.25, 'momentum': 0.20, 'record': 0.15, 'timing': 0.15, 'news': 0.25}
STRONG_BUY, BUY, AVOID = 70, 60, 42


def log(*args):
    print(*args, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def http_get(url, tries=4):
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={
            'User-Agent': UA, 'Accept': '*/*', 'Accept-Language': 'en-AU,en;q=0.9'})
        try:
            with urllib.request.urlopen(req, timeout=25) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 404):
                break
        except Exception as e:  # timeouts, resets, DNS
            last = e
        time.sleep(2 * (attempt + 1) + random.random())
    raise RuntimeError(f'{last}')


def fetch_prices(symbol):
    now = int(time.time())
    qs = urllib.parse.urlencode({
        'period1': now - int(HISTORY_YEARS * 365.25 * 86400), 'period2': now + 86400,
        'interval': '1d', 'events': 'div', 'includeAdjustedClose': 'true'})
    err = None
    for host in ('query1', 'query2'):
        url = f'https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{qs}'
        try:
            return parse_chart(json.loads(http_get(url, tries=3)))
        except Exception as e:
            err = e
    raise RuntimeError(f'prices for {symbol}: {err}')


def parse_chart(data):
    chart = data.get('chart') or {}
    if chart.get('error'):
        raise RuntimeError(chart['error'].get('description') or 'chart error')
    res = (chart.get('result') or [None])[0]
    if not res or not res.get('timestamp'):
        raise RuntimeError('no price data')
    meta = res.get('meta') or {}
    offset = meta.get('gmtoffset') or 0
    closes = res['indicators']['quote'][0].get('close') or []
    adj = ((res['indicators'].get('adjclose') or [{}])[0].get('adjclose')) or closes
    dates, close, adjusted = [], [], []
    for ts, c, a in zip(res['timestamp'], closes, adj):
        if c is None or a is None or c <= 0 or a <= 0:
            continue
        day = datetime.fromtimestamp(ts + offset, tz=timezone.utc).date()
        if dates and day <= dates[-1]:        # repeated or out-of-order bar
            if day == dates[-1]:
                close[-1], adjusted[-1] = float(c), float(a)
            continue
        dates.append(day)
        close.append(float(c))
        adjusted.append(float(a))
    divs = []
    for ev in ((res.get('events') or {}).get('dividends') or {}).values():
        try:
            divs.append((datetime.fromtimestamp(ev['date'] + offset, tz=timezone.utc).date(), float(ev['amount'])))
        except (KeyError, TypeError, ValueError):
            pass
    if len(close) < 60:
        raise RuntimeError('too little price history')
    return {'dates': dates, 'close': close, 'adj': adjusted, 'divs': sorted(divs), 'meta': meta}


def fetch_news(query):
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({
        'q': f'{query} when:{NEWS_DAYS}d', 'hl': 'en-AU', 'gl': 'AU', 'ceid': 'AU:en'})
    return parse_rss(http_get(url))


def parse_rss(xml_bytes, now=None):
    root = ET.fromstring(xml_bytes)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=NEWS_DAYS)
    out, seen = [], set()
    for item in root.iter('item'):
        title = (item.findtext('title') or '').strip()
        src = item.find('source')
        source = (src.text or '').strip() if src is not None else ''
        if source and title.endswith(' - ' + source):
            title = title[:-len(source) - 3].strip()
        try:
            when = parsedate_to_datetime(item.findtext('pubDate') or '')
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        key = re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip()
        if not title or when < cutoff or key in seen:
            continue
        seen.add(key)
        out.append({'title': title, 'source': source,
                    'url': (item.findtext('link') or '').strip(), 'date': when})
    out.sort(key=lambda n: n['date'], reverse=True)
    return out[:40]


# --------------------------------------------------------------------------
# Headline sentiment: a finance word list, so every score can be explained
# by the words that produced it.
# --------------------------------------------------------------------------

LEXICON = {
    # results and guidance
    'record profit': 2, 'record earnings': 2, 'record revenue': 1.5, 'record result': 1.8,
    'beats': 1.5, 'beat expectations': 2, 'beats expectations': 2, 'tops forecasts': 1.5,
    'above expectations': 1.5, 'ahead of expectations': 1.5, 'better than expected': 1.5,
    'raises guidance': 2, 'lifts guidance': 2, 'upgrades guidance': 2, 'raises forecast': 2,
    'lifts forecast': 2, 'raises outlook': 2, 'profit rises': 1.5, 'profit jumps': 1.8,
    'profit up': 1.5, 'profit surges': 2, 'earnings up': 1.2, 'revenue up': 1,
    'misses': -1.5, 'missed': -1.2, 'below expectations': -1.5, 'worse than expected': -1.5,
    'disappoints': -1.5, 'disappointing': -1.5, 'cuts guidance': -2, 'lowers guidance': -2,
    'downgrades guidance': -2, 'cuts forecast': -2, 'profit warning': -2.5,
    'earnings warning': -2, 'profit falls': -1.5, 'profit slumps': -2, 'profit drops': -1.5,
    'guidance cut': -2, 'profit downgrade': -2, 'earnings downgrade': -2, 'shortfall': -1, 'writedown': -1.5, 'write-down': -1.5, 'impairment': -1.5,
    # analysts
    'upgrade': 1.5, 'upgrades': 1.5, 'upgraded': 1.5, 'outperform': 1.2, 'overweight': 1,
    'buy rating': 1.5, 'top pick': 1.5, 'target raised': 1.2, 'raises target': 1.2,
    'downgrade': -1.5, 'downgrades': -1.5, 'downgraded': -1.5, 'underperform': -1.2,
    'underweight': -1, 'sell rating': -1.5, 'target cut': -1.2, 'cuts target': -1.2,
    # price moves
    'record high': 1.5, 'all-time high': 1.5, '52-week high': 1.2, 'highest': 0.6,
    'surge': 1.5, 'surges': 1.5, 'surged': 1.5, 'soar': 1.5, 'soars': 1.5, 'soared': 1.5,
    'jumps': 1.2, 'jumped': 1.2, 'rally': 1, 'rallies': 1, 'rallied': 1, 'climbs': 1,
    'climbed': 1, 'gains': 0.8, 'gained': 0.8, 'rises': 0.8, 'rose': 0.8, 'higher': 0.5,
    'rise': 0.6, 'rebound': 0.8, 'rebounds': 0.8, 'recovery': 0.6, 'recovers': 0.6, 'outperforms': 1.2,
    'record low': -1.5, '52-week low': -1.2, 'multi-year low': -1.2, 'lowest': -0.8,
    'plunge': -2, 'plunges': -2, 'plunged': -2, 'tumble': -1.5, 'tumbles': -1.5,
    'tumbled': -1.5, 'slump': -1.5, 'slumps': -1.5, 'slumped': -1.5, 'crash': -2,
    'crashes': -2, 'sinks': -1.2, 'sank': -1.2, 'falls': -0.8, 'fell': -0.8, 'fall': -0.6,
    'drops': -0.8, 'dropped': -0.8, 'drop': -0.6, 'slides': -0.8, 'slid': -0.8,
    'declines': -0.8, 'decline': -0.6, 'lower': -0.5, 'sell-off': -1.2, 'selloff': -1.2,
    'rout': -1.5, 'slashes': -1.2,
    # business news
    'dividend increase': 1.5, 'raises dividend': 1.5, 'lifts dividend': 1.5,
    'special dividend': 1.5, 'buyback': 1, 'buy-back': 1, 'contract win': 1.2, 'wins': 0.8,
    'awarded': 0.8, 'approval': 0.8, 'approved': 0.8, 'breakthrough': 1.2, 'milestone': 0.6,
    'partnership': 0.5, 'takeover offer': 1, 'takeover bid': 1, 'expands': 0.6,
    'expansion': 0.5, 'growth': 0.5, 'boost': 0.8, 'boosts': 0.8, 'boosted': 0.8,
    'strong': 0.8, 'stronger': 0.8, 'robust': 0.8, 'upbeat': 1, 'optimistic': 0.8,
    'bullish': 1, 'tailwind': 0.8, 'tailwinds': 0.8,
    'dividend cut': -1.5, 'cuts dividend': -1.5, 'losses': -1, 'loss': -1,
    'lawsuit': -1.5, 'class action': -1.8, 'sued': -1.5, 'sues': -1.2, 'probe': -1.5,
    'investigation': -1.2, 'fined': -1.5, 'penalty': -1.2, 'scandal': -2, 'fraud': -2.5,
    'data breach': -2, 'cyber attack': -1.8, 'cyberattack': -1.8, 'breach': -1.2,
    'hack': -1.2, 'outage': -1.2, 'recall': -1, 'layoffs': -1, 'job cuts': -1,
    'redundancies': -0.8, 'strike': -0.8, 'suspended': -1, 'delay': -0.8, 'delayed': -0.8,
    'resigns': -1, 'quits': -1, 'concern': -0.6, 'concerns': -0.6, 'fears': -0.8,
    'worries': -0.8, 'pressure': -0.5, 'headwinds': -0.8, 'uncertainty': -0.6,
    'bearish': -1, 'warns': -1.2, 'warning': -1, 'slowdown': -0.8, 'downturn': -1,
    'recession': -1, 'weak': -0.8, 'weaker': -0.8, 'tariffs': -0.5, 'short seller': -1.2,
    'asic': -0.8, 'accc': -0.6, 'austrac': -1.2,
}
NEGATORS = {'no', 'not', 'never', 'avoids', 'avoid', 'without', 'fails', 'failed', "doesn't", "won't"}
_TERM_RE = re.compile(r'(?<![\w-])(' + '|'.join(
    re.escape(t) for t in sorted(LEXICON, key=len, reverse=True)) + r')(?![\w-])', re.I)


def headline_sentiment(title):
    """Score in -1..1, plus the words that produced it."""
    raw, terms = 0.0, []
    low = title.lower()
    despite = re.search(r'\b(despite|even as)\b', low)
    for m in _TERM_RE.finditer(title):
        term = m.group(1).lower()
        weight = LEXICON[term]
        neg = next((w for w in re.findall(r"[a-z']+", low[:m.start()])[-2:] if w in NEGATORS), None)
        if neg:
            weight *= -0.7
            term = f'{neg} {term}'
        if despite and m.start() > despite.start():   # "falls despite record profit": the fall is the news
            weight *= 0.25
        raw += weight
        terms.append(term)
    return math.tanh(raw / 2), terms


# --------------------------------------------------------------------------
# Price maths
# --------------------------------------------------------------------------

def lookback(dates, days):
    """Index of the last trading day at least `days` before the latest one."""
    target = dates[-1] - timedelta(days=days)
    if dates[0] > target + timedelta(days=7):
        return None
    return max(bisect.bisect_right(dates, target) - 1, 0)


def ret_since(px, days):
    i = lookback(px['dates'], days)
    return px['adj'][-1] / px['adj'][i] - 1 if i is not None else None


def cagr(px, years):
    i = lookback(px['dates'], round(365.25 * years))
    if i is None:
        return None
    yrs = (px['dates'][-1] - px['dates'][i]).days / 365.25
    return (px['adj'][-1] / px['adj'][i]) ** (1 / yrs) - 1 if yrs > 0.5 else None


def sma_series(values, n):
    out, total = [None] * len(values), 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def rsi(close, n=14):
    c = close[-300:]
    if len(c) <= n:
        return None
    gain = sum(max(c[i] - c[i - 1], 0) for i in range(1, n + 1)) / n
    loss = sum(max(c[i - 1] - c[i], 0) for i in range(1, n + 1)) / n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def volatility(adj):
    a = adj[-253:]
    r = [math.log(a[i] / a[i - 1]) for i in range(1, len(a))]
    if len(r) < 20:
        return None
    mean = sum(r) / len(r)
    return math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252)


def max_drawdown(adj):
    peak, worst = adj[0], 0.0
    for v in adj:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def up_months(px):
    month_end = {}
    for d, v in zip(px['dates'], px['adj']):
        month_end[(d.year, d.month)] = v
    vals = [month_end[k] for k in sorted(month_end)][-61:]
    changes = [b / a - 1 for a, b in zip(vals, vals[1:])]
    if len(changes) < 12:
        return None, 0
    return sum(1 for c in changes if c > 0) / len(changes), len(changes)


def last_cross(dates, s50, s200, within=130):
    """Most recent 50/200-day crossover in the last `within` trading days."""
    for i in range(len(dates) - 1, max(len(dates) - within, 1), -1):
        a, b = s50[i - 1], s200[i - 1]
        if None in (a, b, s50[i], s200[i]):
            break
        if (a - b) * (s50[i] - s200[i]) < 0:
            return dates[i], 'golden' if s50[i] > s200[i] else 'death'
    return None, None


def perf(px):
    return {
        'r1m': ret_since(px, 30), 'r3m': ret_since(px, 91), 'r6m': ret_since(px, 182),
        'r1y': ret_since(px, 365), 'cagr3': cagr(px, 3), 'cagr5': cagr(px, 5),
    }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def lin(x, x0, x1):
    """Map x0 -> 0 and x1 -> 100, clamped."""
    if x is None:
        return None
    return min(max((x - x0) / (x1 - x0), 0.0), 1.0) * 100


def piecewise(x, points):
    if x is None:
        return None
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def wavg(pairs):
    pairs = [(s, w) for s, w in pairs if s is not None]
    if not pairs:
        return 50.0
    return sum(s * w for s, w in pairs) / sum(w for _, w in pairs)


RSI_POINTS = [(15, 40), (30, 65), (45, 90), (60, 85), (70, 55), (80, 20), (90, 10)]
OFF_HIGH_POINTS = [(-0.45, 10), (-0.25, 35), (-0.12, 85), (-0.04, 85), (0, 65)]


def score_news(news):
    if not news:
        return 50.0, {'n': 0, 'pos': 0, 'neg': 0, 'tone': 'quiet', 'avg': 0.0}
    now = max(n['date'] for n in news)
    wsum = ssum = 0.0
    for n in news:
        w = math.exp(-(now - n['date']).total_seconds() / 86400 / 12)
        wsum += w
        ssum += w * n['sentiment']
    avg = ssum / wsum
    pos = sum(1 for n in news if n['sentiment'] > 0.15)
    neg = sum(1 for n in news if n['sentiment'] < -0.15)
    raw = lin(avg, -0.5, 0.5)
    score = 50 + (raw - 50) * min(1.0, len(news) / 6)     # few headlines -> stay near neutral
    tone = 'positive' if avg > 0.12 else 'negative' if avg < -0.12 else 'mixed'
    return score, {'n': len(news), 'pos': pos, 'neg': neg, 'tone': tone, 'avg': avg}


# --------------------------------------------------------------------------
# Writing it up
# --------------------------------------------------------------------------

def pct(x, signed=True, dp=1):
    s = f'{x * 100:+.{dp}f}%' if signed else f'{x * 100:.{dp}f}%'
    return s.replace('-', '−')


def day(d):
    return f'{d.day} {d.strftime("%b")}'


def clip(text, n=110):
    return text if len(text) <= n else text[:n - 1].rstrip() + '…'


def analyse(entry, symbol, px, news, bench_px, bench_perf, bench_name):
    dates, close = px['dates'], px['close']
    last = close[-1]
    s50, s200 = sma_series(close, 50), sma_series(close, 200)
    sma50, sma200 = s50[-1], s200[-1]
    prev200 = s200[-21] if len(s200) > 21 else None
    slope200 = sma200 / prev200 - 1 if sma200 and prev200 else None
    i52 = lookback(dates, 365) or 0
    high52, low52 = max(close[i52:]), min(close[i52:])
    p = perf(px)
    i1, i12 = lookback(dates, 30), lookback(dates, 365)
    mom = px['adj'][i1] / px['adj'][i12] - 1 if i1 is not None and i12 is not None else None
    is_bench = bench_px is not None and px is bench_px
    rel6 = p['r6m'] - bench_perf['r6m'] if bench_perf and None not in (p['r6m'], bench_perf.get('r6m')) else None
    long_ret, long_yrs = (p['cagr5'], 5) if p['cagr5'] is not None else (p['cagr3'], 3) if p['cagr3'] is not None else (None, None)
    upm, n_months = up_months(px)
    mdd = max_drawdown(px['adj'])
    years_held = (dates[-1] - dates[0]).days / 365.25
    vol = volatility(px['adj'])
    r14 = rsi(close)
    off_high = last / high52 - 1
    div12 = sum(a for d, a in px['divs'] if d > dates[-1] - timedelta(days=365))
    dy = div12 / last if div12 else 0.0
    cross_date, cross_kind = last_cross(dates, s50, s200)

    for n in news or []:
        n['sentiment'], n['terms'] = headline_sentiment(n['title'])
    news_score, nstat = score_news(news or [])

    comp = {
        'trend': wavg([(lin(last / sma200 - 1 if sma200 else None, -0.15, 0.15), 0.45),
                       (lin(sma50 / sma200 - 1 if sma50 and sma200 else None, -0.05, 0.05), 0.35),
                       (lin(slope200, -0.02, 0.02), 0.20)]),
        'momentum': wavg([(lin(mom, -0.20, 0.30), 0.4), (lin(rel6, -0.15, 0.15), 0.3),
                          (lin(p['r3m'], -0.10, 0.15), 0.3)]),
        'record': wavg([(lin(long_ret, -0.05, 0.15), 0.45), (lin(upm, 0.40, 0.65), 0.25),
                        (lin(mdd, -0.60, -0.15), 0.30)]),
        'timing': wavg([(piecewise(r14, RSI_POINTS), 0.5), (piecewise(off_high, OFF_HIGH_POINTS), 0.5)]),
        'news': news_score,
    }
    score = round(sum(comp[k] * w for k, w in WEIGHTS.items()))

    up = sma200 is not None and last > sma200
    golden = sma50 is not None and sma200 is not None and sma50 > sma200
    if score >= STRONG_BUY and up and golden and comp['news'] >= 50:
        signal = 'strong-buy'
    elif score >= BUY and up and comp['news'] >= 40:
        signal = 'buy'
    elif score < AVOID or (not up and not golden and comp['momentum'] < 35):
        signal = 'avoid'
    else:
        signal = 'watch'

    # ---- reasons -------------------------------------------------------
    points = []

    def add(tone, factor, text):
        points.append({'tone': tone, 'factor': factor, 'text': text})

    d200 = last / sma200 - 1 if sma200 else None
    cross_txt = ''
    if cross_kind == 'golden' and golden:
        cross_txt = f' (it crossed above on {day(cross_date)})'
    if d200 is None:
        add('neutral', 'Trend', 'Not enough history yet for a 200-day average.')
    elif up and golden:
        add('pos', 'Trend', f'Uptrend: the price is {pct(d200, False)} above its 200-day average and the 50-day average is above the 200-day{cross_txt}.')
    elif up:
        add('neutral', 'Trend', f'The price is {pct(d200, False)} above its 200-day average, but the 50-day average is still below the 200-day, so the recovery isn’t confirmed yet.')
    elif golden:
        add('neg', 'Trend', f'The price has slipped {pct(-d200, False)} below its 200-day average, although the 50-day average is still above the 200-day.')
    else:
        extra = f' (it crossed below on {day(cross_date)})' if cross_kind == 'death' else ''
        add('neg', 'Trend', f'Downtrend: the price is {pct(-d200, False)} below its 200-day average and the 50-day average is below the 200-day{extra}.')
    if slope200 is not None and slope200 < -0.01 and up:
        add('neg', 'Trend', 'The 200-day average itself is still falling.')

    r1y, b1y = p['r1y'], (bench_perf or {}).get('r1y')
    if r1y is not None:
        if is_bench or b1y is None:
            add('pos' if r1y > 0.08 else 'neg' if r1y < -0.05 else 'neutral', 'Performance',
                f'Returned {pct(r1y)} over the last 12 months, including dividends.')
        else:
            diff = r1y - b1y
            tone = 'pos' if diff > 0.02 and r1y > 0 else 'neg' if diff < -0.05 or r1y < -0.10 else 'neutral'
            verb = 'beating' if diff > 0.02 else 'lagging' if diff < -0.02 else 'in line with'
            add(tone, 'Performance', f'Returned {pct(r1y)} over the last 12 months including dividends, {verb} the {bench_name} ({pct(b1y)}).')
    if p['r3m'] is not None and abs(p['r3m']) >= 0.08:
        add('pos' if p['r3m'] > 0 else 'neg', 'Momentum', f'{"Up" if p["r3m"] > 0 else "Down"} {pct(abs(p["r3m"]), False)} over the last 3 months.')

    if long_ret is not None:
        tone = 'pos' if long_ret >= 0.08 else 'neg' if long_ret < 0.02 else 'neutral'
        add(tone, 'Track record', f'Over {long_yrs} years it has returned {pct(long_ret)} a year including dividends'
            + (f', with {upm * 100:.0f}% of months positive.' if upm is not None else '.'))
    elif years_held < 3:
        add('neutral', 'Track record', f'Only {years_held:.1f} years of price history, so the long-term record counts for less.')
    if mdd < -0.35:
        add('neg', 'Risk', f'Its worst fall from a peak in the last {min(years_held, 5):.0f} years was {pct(-mdd, False, 0)}.')
    if vol is not None and vol > 0.35:
        add('neg', 'Risk', f'Big price swings: {vol * 100:.0f}% annualised volatility (the ASX 200 is usually 12–18%).')

    if r14 is not None:
        if r14 >= 70:
            add('neg', 'Timing', f'Looks stretched short term: RSI is {r14:.0f} (above 70 is overbought), and pullbacks are common from here.')
        elif r14 <= 30:
            add('neutral', 'Timing', f'Oversold short term (RSI {r14:.0f}). It can bounce, but heavy selling usually has a reason.')
        elif 40 <= r14 <= 62 and up:
            add('pos', 'Timing', f'Not overheated: RSI is {r14:.0f}, so you wouldn’t be buying into a spike.')
    if up and -0.15 <= off_high <= -0.04:
        add('pos', 'Timing', f'{pct(-off_high, False)} below its 52-week high: a pullback inside an uptrend.')
    elif off_high > -0.02:
        add('neutral', 'Timing', 'Trading at or near its 52-week high.')
    elif off_high < -0.25:
        add('neg', 'Timing', f'{pct(-off_high, False, 0)} below its 52-week high.')

    if dy >= 0.03:
        add('pos', 'Income', f'Paid {pct(dy, False)} in cash dividends over the last year (before franking credits).')

    topic = entry.get('topic') or entry['name']
    ranked = sorted(news or [], key=lambda n: n['sentiment'])
    best = ranked[-1] if ranked and ranked[-1]['sentiment'] > 0.15 else None
    worst = ranked[0] if ranked and ranked[0]['sentiment'] < -0.15 else None
    if news is None:
        add('neutral', 'News', 'Couldn’t load headlines on this run, so news is scored as neutral.')
    elif not news:
        add('neutral', 'News', f'No headlines about {topic} in the last {NEWS_DAYS} days, so news is scored as neutral.')
    else:
        tone = {'positive': 'pos', 'negative': 'neg'}.get(nstat['tone'], 'neutral')
        add(tone, 'News', f'News about {topic} over the last {NEWS_DAYS} days is {nstat["tone"]}: '
            f'{nstat["pos"]} positive and {nstat["neg"]} negative of {nstat["n"]} headlines.')
        if best:
            add('pos', 'News', f'“{clip(best["title"])}” ({best["source"] or "news"}, {day(best["date"])})')
        if worst:
            add('neg', 'News', f'“{clip(worst["title"])}” ({worst["source"] or "news"}, {day(worst["date"])})')

    # ---- summary paragraph --------------------------------------------
    name = entry['name']
    if d200 is None:
        trend_phrase = 'is too new for a long-term trend'
    elif up and golden:
        trend_phrase = 'is in an uptrend'
    elif up:
        trend_phrase = 'has climbed back above its 200-day average'
    elif golden:
        trend_phrase = 'has dipped below its 200-day average inside a longer uptrend'
    else:
        trend_phrase = 'is in a downtrend'
    if r1y is None:
        perf_phrase = 'has less than a year of history'
    elif is_bench or b1y is None:
        perf_phrase = f'has returned {pct(r1y)} over the past year'
    else:
        diff = r1y - b1y
        word = 'beaten' if diff > 0.02 else 'lagged' if diff < -0.02 else 'kept pace with'
        perf_phrase = f'has {word} the {bench_name} over the past year ({pct(r1y)} vs {pct(b1y)})'
    parts = [f'{name} {trend_phrase} and {perf_phrase}.']
    if news:
        lead = best if signal in ('buy', 'strong-buy') else (worst or best)
        s = (f'News about {topic} over the last month is {nstat["tone"]} '
             f'({nstat["pos"]} positive, {nstat["neg"]} negative of {nstat["n"]} headlines)')
        if lead:
            s += f', including “{clip(lead["title"], 90)}”'
        parts.append(s + '.')
    else:
        parts.append(f'There’s been little news about {topic} lately, so news doesn’t move the score.')
    if long_ret is not None:
        parts.append(f'Over {long_yrs} years it has returned {pct(long_ret)} a year.')
    if signal in ('buy', 'strong-buy'):
        # The first sentence already covers 12-month performance, so name a different risk.
        negs = [pt for pt in points if pt['tone'] == 'neg']
        risk = next((pt['text'] for f in ('Risk', 'Timing', 'Trend', 'Momentum', 'Track record')
                     for pt in negs if pt['factor'] == f), None)
        if risk:
            parts.append('Main risk: ' + risk[0].lower() + risk[1:])
    else:
        needs = []
        if not up:
            needs.append('the price back above its 200-day average')
        if comp['news'] < 40:
            needs.append('better news')
        if score < BUY:
            needs.append(f'a higher overall score ({score} now; a Buy needs {BUY})')
        if needs:
            parts.append('To turn into a Buy it needs ' + ', '.join(needs[:-1]) + (' and ' if len(needs) > 1 else '') + needs[-1] + '.')

    # ---- chart: two years of weekly points ------------------------------
    idx = list(range(len(close) - 1, max(len(close) - 1 - 520, -1), -5))[::-1]
    chart = {
        'dates': [dates[i].isoformat() for i in idx],
        'close': [r4(close[i]) for i in idx],
        'sma200': [r4(s200[i]) if s200[i] else None for i in idx],
    }

    return {
        'code': entry['code'], 'symbol': symbol, 'name': name, 'type': entry.get('type', 'share'),
        'sector': entry.get('sector', ''), 'topic': topic, 'currency': (px['meta'].get('currency') or 'AUD'),
        'asOf': dates[-1].isoformat(), 'since': dates[0].isoformat(),
        'price': r4(last), 'change1d': r4(close[-1] / close[-2] - 1) if len(close) > 1 else None,
        'signal': signal, 'score': score,
        'components': {k: round(v) for k, v in comp.items()},
        'metrics': {**{k: r4(v) for k, v in p.items()},
                    'sma50': r4(sma50), 'sma200': r4(sma200), 'rsi': r1(r14),
                    'high52': r4(high52), 'low52': r4(low52), 'offHigh': r4(off_high),
                    'volatility': r4(vol), 'maxDrawdown': r4(mdd), 'upMonths': r4(upm),
                    'months': n_months, 'divYield': r4(dy), 'momentum12_1': r4(mom),
                    'cross': {'kind': cross_kind, 'date': cross_date.isoformat()} if cross_date else None},
        'newsStats': {k: (r4(v) if isinstance(v, float) else v) for k, v in nstat.items()},
        'summary': ' '.join(parts),
        'points': points,
        'news': [{'title': n['title'], 'source': n['source'], 'url': n['url'],
                  'date': n['date'].isoformat(), 'sentiment': r4(n['sentiment']), 'terms': n['terms']}
                 for n in (news or [])[:12]],
        'chart': chart,
    }


def r4(x):
    return None if x is None else float(f'{x:.4g}') if abs(x) < 1 else round(x, 4)


def r1(x):
    return None if x is None else round(x, 1)


# --------------------------------------------------------------------------

def load_previous(path):
    try:
        with open(path) as f:
            return {i['code']: i for i in json.load(f).get('items', [])}
    except (OSError, ValueError):
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--watchlist', default=WATCHLIST)
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--only', help='comma-separated codes to build, e.g. BHP,VAS')
    args = ap.parse_args(argv)

    with open(args.watchlist) as f:
        wl = json.load(f)
    only = {c.strip().upper() for c in args.only.split(',')} if args.only else None
    previous = load_previous(args.out)
    bcfg = wl['benchmark']
    bench_name = bcfg.get('name', 'ASX 200')

    try:
        bench_px = fetch_prices(bcfg['symbol'])
        bench_perf = perf(bench_px)
        log(f'benchmark {bcfg["symbol"]}: {len(bench_px["dates"])} days to {bench_px["dates"][-1]}')
    except Exception as e:
        log(f'benchmark {bcfg["symbol"]} failed: {e}')
        bench_px, bench_perf = None, None

    news_cache, items, failed = {}, [], []
    for entry in wl['items']:
        code = entry['code']
        if only and code not in only:
            if code in previous:
                items.append(previous[code])
            continue
        symbol = entry.get('symbol') or f'{code}.AX'
        try:
            if bench_px is not None and symbol == bcfg['symbol']:
                px = bench_px
            else:
                px = fetch_prices(symbol)
                time.sleep(PAUSE)
            query = entry.get('news') or f'"{entry["name"]}"'
            if query not in news_cache:
                try:
                    news_cache[query] = fetch_news(query)
                except Exception as e:
                    log(f'{code}: news failed: {e}')
                    news_cache[query] = None
                time.sleep(PAUSE)
            cached = news_cache[query]
            news = None if cached is None else [dict(n) for n in cached]
            item = analyse(entry, symbol, px, news, bench_px, bench_perf, bench_name)
            items.append(item)
            log(f'{code:5} {item["signal"]:10} {item["score"]:3}  news {item["newsStats"]["n"]:2}  {item["asOf"]}')
        except Exception as e:
            log(f'{code}: FAILED: {e}')
            if not isinstance(e, RuntimeError):
                traceback.print_exc()
            failed.append(code)
            if code in previous:
                items.append({**previous[code], 'stale': True})

    fresh = [i for i in items if not i.get('stale')]
    if not fresh:
        log('Nothing could be fetched, so the previous file is left as it is.')
        return 1

    items.sort(key=lambda i: (-i['score'], i['code']))
    out = {
        'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'asOf': max(i['asOf'] for i in fresh),
        'benchmark': {'symbol': bcfg['symbol'], 'name': bench_name,
                      **({k: r4(v) for k, v in bench_perf.items()} if bench_perf else {})},
        'method': {'weights': WEIGHTS, 'strongBuy': STRONG_BUY, 'buy': BUY, 'avoid': AVOID,
                   'newsDays': NEWS_DAYS},
        'failed': failed,
        'items': items,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = args.out + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, args.out)
    counts = {s: sum(1 for i in items if i['signal'] == s) for s in ('strong-buy', 'buy', 'watch', 'avoid')}
    log(f'wrote {args.out}: {len(items)} items, {counts}, failed: {failed or "none"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
