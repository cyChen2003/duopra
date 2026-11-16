"""Stock/market quotes and ECB reference-rate adapters.

Results are model-facing; chart series and provenance are UI-only. Network I/O
uses the shared tool transport. Sources are Tencent quote feeds and Frankfurter.
"""
import json
import copy
import math
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

class LiveError(Exception):
    """Raised with a runtime error code; the executor wraps it as live_unavailable."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# market prefix -> (market, currency, exchange time zone, display suffix)
MARKETS = {
    "sh": ("cn", "CNY", "Asia/Shanghai", ".SH"),
    "sz": ("cn", "CNY", "Asia/Shanghai", ".SZ"),
    "bj": ("cn", "CNY", "Asia/Shanghai", ".BJ"),
    "hk": ("hk", "HKD", "Asia/Hong_Kong", ".HK"),
    "us": ("us", "USD", "America/New_York", ""),
}
TRADABLE = {"GP-A", "GP-B", "GP", "ZS", "ETF", "LOF"}
INDICES = {
    "cn": [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"), ("sh000300", "沪深300")],
    "hk": [("hkHSI", "恒生指数"), ("hkHSTECH", "恒生科技指数"), ("hkHSCEI", "国企指数")],
    "us": [("usDJI", "道琼斯"), ("usIXIC", "纳斯达克"), ("usINX", "标普500")],
}
# Tencent's market-status keys -> our markets.
STATUS_KEYS = {"cn": "SH", "hk": "HK", "us": "US"}


def _num(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def resolve(query, fetch):
    """Name or code -> (tencent quote code, prefix, display symbol, name, type)."""
    text = query.strip()
    if not text or len(text) > 40:
        raise LiveError("invalid_query", "query must be 1-40 characters")
    explicit = re.fullmatch(r"(?i)(\d{6})\.(SH|SZ|BJ)|(\d{4,5})\.HK|([A-Z][A-Z.]{0,9})\.US", text)
    if explicit:
        if explicit.group(1):
            prefix = explicit.group(2).lower()
            return f"{prefix}{explicit.group(1)}", prefix, f"{explicit.group(1)}.{explicit.group(2).upper()}", None, None
        if explicit.group(3):
            code = explicit.group(3).zfill(5)
            return f"hk{code}", "hk", f"{code}.HK", None, None
        ticker = explicit.group(4).upper()
        return f"us{ticker}", "us", ticker, None, None
    url = "https://smartbox.gtimg.cn/s3/?v=2&t=all&q=" + urllib.parse.quote(text)
    body = fetch(url)
    hint = re.search(r'v_hint="(.*)"', body, re.S)
    if not hint or hint.group(1) in ("", "N;"):
        raise LiveError("unknown_symbol", f"No security matches {text!r}")
    entries = [item.split("~") for item in json.loads(f'"{hint.group(1)}"').split("^")]
    candidates = [e for e in entries if len(e) >= 5 and e[0] in MARKETS and e[4] in TRADABLE]
    if not candidates:
        raise LiveError("unknown_symbol", f"No tradable security matches {text!r}")
    # Stocks before ETFs before indices ("腾讯" means 腾讯控股, not the 腾讯济安 index) unless
    # the user asked for an index; an exact name wins within a rank, then the provider's order.
    wants_index = "指数" in text or text.lower().startswith(".")
    rank = {"ZS": 0} if wants_index else {"GP-A": 0, "GP": 0, "GP-B": 1, "ETF": 2, "LOF": 2, "ZS": 3}
    ordered = sorted(enumerate(candidates), key=lambda item: (
        item[1][1].split('.')[0].casefold() != text.casefold(),
        rank.get(item[1][4], 4), item[1][2] != text, item[0]))
    prefix, code, name, _, kind = ordered[0][1]
    if prefix == "us":
        ticker = code.split(".")[0].upper()
        return f"us{ticker}", "us", ticker, name, kind
    if prefix == "hk":
        return f"hk{code}", "hk", f"{code}.HK", name, kind
    return f"{prefix}{code}", prefix, f"{code}{MARKETS[prefix][3]}", name, kind


def _as_of(text, prefix):
    zone = ZoneInfo(MARKETS[prefix][2])
    for pattern in ("%Y%m%d%H%M%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=zone).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def quotes(codes, fetch):
    """Batch quote: {tencent code: fields}. Field layout is shared by A-share, HK and US."""
    body = fetch("https://qt.gtimg.cn/q=" + ",".join(codes), encoding="gbk")
    out = {}
    for code, raw in re.findall(r'v_([A-Za-z0-9.]+)="([^"]*)"', body):
        fields = raw.split("~")
        if len(fields) < 35 or _num(fields[3]) is None or _num(fields[3]) <= 0:
            continue
        prefix = code[:2]
        if prefix not in MARKETS:
            continue
        out[code] = {
            "name": fields[1],
            "code": fields[2],
            "price": _num(fields[3]),
            "prev_close": _num(fields[4]),
            "open": _num(fields[5]),
            "as_of": _as_of(fields[30], prefix),
            "change": _num(fields[31]),
            "change_pct": _num(fields[32]),
            "high": _num(fields[33]),
            "low": _num(fields[34]),
        }
    return out


def intraday(code, prefix, quote_code_field, fetch):
    """Today's 1-minute closes [[HH:MM, price], ...] and the market's open/closed state."""
    if prefix == "us":
        symbol = "us" + (quote_code_field if quote_code_field.startswith(".") else quote_code_field)
        url = "https://web.ifzq.gtimg.cn/appstock/app/UsMinute/query?code=" + urllib.parse.quote(symbol)
    else:
        symbol = code
        url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=" + urllib.parse.quote(symbol)
    try:
        payload = json.loads(fetch(url))
        node = payload["data"][symbol]
    except Exception:
        # A missing/unavailable chart must not turn a valid quote into failure.
        # Empty data is displayed as unavailable, never synthesized.
        return [], None
    points = []
    for row in node.get("data", {}).get("data", []):
        parts = row.split()
        if len(parts) >= 2 and re.fullmatch(r"\d{4}", parts[0]) and _num(parts[1]):
            points.append([f"{parts[0][:2]}:{parts[0][2:]}", _num(parts[1])])
    state = None
    market = (node.get("qt", {}).get("market") or [""])[0]
    key = STATUS_KEYS[MARKETS[prefix][0]]
    found = re.search(rf"\|{key}_(open|close)_", market)
    if found:
        state = "open" if found.group(1) == "open" else "closed"
    return points, state


def stock_quote(args, fetch):
    """args: {query}. Example result: 贵州茅台 600519.SH 1349.98 CNY +1.12%."""
    code, prefix, symbol, name, kind = resolve(args["query"], fetch)
    quote = quotes([code], fetch).get(code)
    if not quote:
        raise LiveError("unknown_symbol", f"No quote for {symbol}")
    if not quote['as_of'] or any(quote[k] is None for k in ('price','prev_close','change','change_pct')):
        raise LiveError("upstream_empty", "Quote lacks a valid timestamp or required finite fields")
    points, state = intraday(code, prefix, quote["code"], fetch)
    market, currency, zone, _ = MARKETS[prefix]
    if prefix == "us":
        # Tencent reports AAPL.OQ for stocks and .IXIC for indices.
        symbol = quote["code"] if quote["code"].startswith(".") else quote["code"].split(".")[0]
    result = {
        "name": quote["name"] or name or symbol,
        "symbol": symbol,
        "market": market,
        "currency": currency,
        "price": quote["price"],
        "change": quote["change"],
        "change_pct": quote["change_pct"],
        "open": quote["open"],
        "high": quote["high"],
        "low": quote["low"],
        "prev_close": quote["prev_close"],
        "as_of": quote["as_of"],
        "market_state": state or "unknown",
    }
    result = {key:value for key,value in result.items() if value is not None}
    provenance = {
        "source": "live_tencent_quote",
        "resolved": {"query": args["query"], "symbol": result["symbol"], "name": result["name"], "type": kind},
        "chart": {"prev_close": quote["prev_close"], "points": points, "time_zone": zone},
        "notice": "行情来自公开报价接口，可能有延迟；仅供参考，不构成投资建议。",
        "urls": ["https://qt.gtimg.cn/q=" + code],
        "timestamp_semantics": "provider_exchange_local_time",
        "chart_available": bool(points),
    }
    return result, provenance


def market_indices(args, fetch):
    """args: {region: cn | hk | us}. A board of the region's main indices."""
    region = args.get("region", "cn")
    if region not in INDICES:
        raise LiveError("invalid_region", "region must be cn, hk or us")
    codes = [code for code, _ in INDICES[region]]
    table = quotes(codes, fetch)
    rows, charts, states = [], {}, []
    usable = {code:quote for code,quote in table.items()
              if all(quote.get(key) is not None for key in ('price','change','change_pct','prev_close'))}
    futures = {code:_CHART_POOL.submit(intraday,code,code[:2],quote['code'],fetch)
               for code,quote in usable.items()}
    for code, label in INDICES[region]:
        quote = usable.get(code)
        if not quote:
            continue
        symbol = quote["code"]
        points, state = futures[code].result()
        states.append(state)
        rows.append({"name": quote["name"] or label, "symbol": symbol, "price": quote["price"], "change": quote["change"], "change_pct": quote["change_pct"]})
        charts[symbol] = {"prev_close": quote["prev_close"], "points": points}
    if not rows:
        raise LiveError("upstream_empty", "No index quotes returned")
    as_of = max((table[c]["as_of"] for c in codes if c in table and table[c]["as_of"]), default=None)
    state = "open" if "open" in states else "closed" if "closed" in states else "unknown"
    result = {"region": region, "market_state": state, "indices": rows}
    if as_of:
        result['as_of'] = as_of
    provenance = {
        "source": "live_tencent_quote",
        "charts": charts,
        "time_zone": MARKETS[codes[0][:2]][2],
        "notice": "行情来自公开报价接口，可能有延迟；仅供参考，不构成投资建议。",
        "urls": ["https://qt.gtimg.cn/q=" + ",".join(codes)],
        "missing_symbols": [code for code in codes if code not in usable],
        "timestamp_semantics": "provider_exchange_local_time",
    }
    return result, provenance


def currency_trend(args, fetch):
    """args: {base, quote, days 7..365}. ECB reference rates on business days."""
    base, quote, days = args["base"].upper(), args["quote"].upper(), args["days"]
    if not (re.fullmatch(r"[A-Z]{3}", base) and re.fullmatch(r"[A-Z]{3}", quote)) or base == quote:
        raise LiveError("invalid_currency", "Currencies must be two different 3-letter ISO codes")
    if type(days) is not int or not 7 <= days <= 365:
        raise LiveError("invalid_range", "days must be 7..365")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = f"https://api.frankfurter.dev/v1/{start.isoformat()}..{end.isoformat()}?base={base}&symbols={quote}"
    data = json.loads(fetch(url))
    if not isinstance(data,dict) or data.get('base') != base or not isinstance(data.get('rates'),dict):
        raise LiveError('upstream_empty','Reference response has the wrong base or invalid rates')
    series = []
    for day,rates in sorted(data['rates'].items()):
        try:
            observed = date.fromisoformat(day)
        except (TypeError,ValueError) as exc:
            raise LiveError('upstream_empty','Reference response contains an invalid date') from exc
        if not isinstance(rates,dict) or quote not in rates:
            continue
        rate = rates[quote]
        if type(rate) not in (int,float) or not math.isfinite(rate) or rate <= 0 or observed > end:
            raise LiveError('upstream_empty','Reference response contains an invalid value or noncausal date')
        # v1 can include the previous working day's boundary quote when the
        # requested start is a weekend. Keep the displayed series strictly
        # inside the user's requested lookback rather than rejecting it.
        if observed < start:
            continue
        series.append([day,rate])
    if len(series) < 2:
        raise LiveError("upstream_empty", "Not enough reference rates in range")
    high = max(series, key=lambda point: point[1])
    low = min(series, key=lambda point: point[1])
    first, last = series[0], series[-1]
    result = {
        "base": base,
        "quote": quote,
        "start": first[0],
        "end": last[0],
        "first": first[1],
        "last": last[1],
        "change_pct": round((last[1] / first[1] - 1) * 100, 2),
        "high": high[1],
        "high_date": high[0],
        "low": low[1],
        "low_date": low[0],
    }
    provenance = {"source": "live_frankfurter_reference", "url": url, "series": series, "not_transaction_quote": True,
                  "base":base,"quote":quote,"requested_start":start.isoformat(),"requested_end":end.isoformat(),
                  "rate_semantics":"quote units per 1 base","reference_frequency":"daily reference rates; not a trading quote"}
    return result, provenance


LIVE_ADAPTERS = {"stock.quote": stock_quote, "market.indices": market_indices, "currency.trend": currency_trend}

_CHART_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='market-chart')
_LOCK = threading.Lock()
_CACHE = OrderedDict()
_INFLIGHT = {}


def execute(name, arguments, fetch):
    """Bounded, copy-on-read public-data cache with per-key single flight."""
    if name not in LIVE_ADAPTERS:
        raise LiveError('unknown_tool','Unknown finance adapter')
    key = (name,json.dumps(arguments,ensure_ascii=False,sort_keys=True),
           datetime.now(timezone.utc).date().isoformat())
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and now-cached[0] < (3600 if name=='currency.trend' else 8):
            result,provenance = copy.deepcopy(cached[1])
            provenance.update(cache_hit=True,cache_age_seconds=round(now-cached[0],3))
            _CACHE.move_to_end(key)
            return result,provenance
        owner = key not in _INFLIGHT
        future = _INFLIGHT.setdefault(key,Future())
    if not owner:
        return copy.deepcopy(future.result(timeout=25))
    try:
        value = LIVE_ADAPTERS[name](arguments,fetch)
        with _LOCK:
            _CACHE[key] = (time.monotonic(),copy.deepcopy(value))
            _CACHE.move_to_end(key)
            while len(_CACHE)>128:
                _CACHE.popitem(last=False)
        future.set_result(copy.deepcopy(value))
        return value
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _LOCK:
            _INFLIGHT.pop(key,None)
