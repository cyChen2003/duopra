"""CPU-only tools for the trained Duopra JSON protocol; never generates chat.

The model owns tool choice and speech. A host calls before_input_unit exactly
once for each *model* audio unit, not for every browser audio packet. Tools run
in a bounded thread pool and results cannot enter a unit at/before submission.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from zoneinfo import ZoneInfo
from discovery_function import NAME as DISCOVERY_NAME, VERSION as DISCOVERY_VERSION, schema as discovery_schema, observed as discovery_observed
from finance_market import LiveError as FinanceError, execute as finance_execute
from personal_client import PERSONAL_TOOLS, execute as personal_execute
from personal_store import PersonalError
from personal_harness import normalize as normalize_personal_action, documentation as personal_documentation, patch_error as personal_patch_error, error_contract as personal_error_contract


ROOT = Path(__file__).resolve().parent
SCHEMA_KEYS = ("name", "description", "excluded", "version", "parameters", "returns", "read_only")
SCHEMA_VERSION_POLICY = "visible_schema_authoritative"
HARNESS_TOOL = "assistant.query"
PRIVATE_SPAN_ERRORS = frozenset({
    'delegate_close_missing', 'private_json_incomplete', 'private_json_invalid',
    'nested_private_span', 'unmatched_delegate_close', 'private_action_too_large',
})
CALL_FORMAT_ERRORS = PRIVATE_SPAN_ERRORS | {'action_fields', 'invalid_private_action', 'invalid_action'}
MAX_FORMAT_RETRIES = 2


def schema_version_metadata(action, effective_version):
    """Audit optional version metadata without granting tool authority.

    The delivered catalog is authoritative. Keep the submitted value intact for
    diagnostics; neither a missing value nor a mismatched value changes the
    current schema's argument validation, visibility or authorization rules.
    """
    return {
        "schema_version_policy": SCHEMA_VERSION_POLICY,
        "schema_version_required": False,
        "requested_schema_version_present": "schema_version" in action,
        "requested_schema_version": copy.deepcopy(action.get("schema_version")),
        "effective_schema_version": effective_version,
        "schema_version_matched": action.get("schema_version") == effective_version,
    }


class ToolError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def strict_json(raw):
    def pairs(items):
        obj = {}
        for k, v in items:
            if k in obj:
                raise ToolError("duplicate_json_key", "JSON contains a duplicate key: " + k)
            obj[k] = v
        return obj
    def constant(value):
        raise ToolError("non_finite_json", "JSON cannot contain " + value)
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except json.JSONDecodeError as exc:
        raise ToolError("invalid_json", "Private action must be one complete JSON object") from exc


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate(value, schema, path="arguments"):
    kind = schema.get("type")
    ok = {"object": type(value) is dict, "string": type(value) is str,
          "integer": type(value) is int, "number": type(value) in (int, float),
          "boolean": type(value) is bool, "array": type(value) is list,
          "null": value is None}.get(kind, False)
    if not ok:
        raise ToolError("schema_type", f"{path} must have type {kind}")
    if kind == "number" and not math.isfinite(value):
        raise ToolError("non_finite_json", path + " must be finite")
    if 'enum' in schema and not any(type(value) is type(item) and value == item for item in schema['enum']):
        raise ToolError('schema_enum', path + ' is outside the allowed values')
    if kind in ('integer','number'):
        if 'minimum' in schema and value < schema['minimum'] or 'maximum' in schema and value > schema['maximum']:
            raise ToolError('schema_range', path + ' is outside the allowed range')
    string_budget = 1600 if schema.get('x-duplex-complete-task') is True else 1000
    if kind == "string" and (len(value) > string_budget or any(ord(c) < 32 and not(schema.get('multiline') and c in '\r\n') for c in value)):
        raise ToolError("schema_string", path + " is too long or contains control characters")
    if kind == 'string' and (len(value) < schema.get('minLength',0) or len(value) > schema.get('maxLength',1000)):
        raise ToolError('schema_string',path + ' has invalid length')
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ToolError("missing_argument", path + " is missing required fields: "+canonical(sorted(set(schema.get('required',[]))-set(value))))
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ToolError("unknown_argument", path + " contains unknown fields: "+canonical(sorted(set(value)-set(props))))
        for key, item in value.items():
            if key in props:
                validate(item, props[key], path + "." + key)
    if kind == "array":
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")


# Semantic intent expansions describe capabilities, not known evaluation queries
# or expected task answers. Retrieval uses query/capability overlap only.
INTENTS = {
 "weather.day":"天气 气温 下雨 降雨 温度 weather temperature rain forecast umbrella",
 "sun.daylight":"日出 日落 白天 sunrise sunset daylight dawn dusk",
 "rail.departures":"高铁 火车 列车 发车 train rail railway departure",
 "bus.next_arrival":"公交 巴士 到站 bus next arrival stop",
 "flight.status":"航班 飞机 延误 flight plane delayed airport",
 "parking.vacancy":"停车 空位 parking vacancy car park spaces",
 "charging.connector":"充电 枪 接口 charging connector charger ev",
 "road.closure":"封路 道路 道路施工 road closure reopen",
 "store.hours":"商店 超市 门店 营业 关门 store shop opening hours close",
 "stock.item":"库存 现货 商品 stock inventory product availability sku",
 "parcel.status":"快递 包裹 物流 parcel package delivery tracking",
 "locker.hours":"快递柜 柜子 locker hours pickup",
 "waste.collection":"垃圾 清运 收集 waste collection rubbish garbage",
 "water.outage":"停水 供水 water outage supply interruption",
 "power.outage":"停电 供电 电力 power electricity outage blackout",
 "laundry.machine":"洗衣 烘干 洗衣房 laundry washer dryer washing machine",
 "library.copy":"图书 借书 馆藏 library book copy borrow isbn",
 "museum.exhibition":"博物馆 展览 museum exhibition gallery",
 "cinema.showtime":"电影院 电影 场次 cinema movie showtime screening",
 "theatre.performance":"剧院 演出 剧场 theatre theater performance play",
 "sports.fixture":"球队 比赛 赛程 sports fixture team match game",
 "park.access":"公园 入口 开放 park entrance access",
 "trail.status":"步道 登山 关闭 trail hiking path closed",
 "pool.lanes":"泳池 泳道 游泳 pool swimming public lanes",
 "calendar.agenda":"日历 日程 安排 calendar agenda schedule",
 "meeting.room":"会议室 空闲 room meeting availability reservation",
 "leave.balance":"年假 剩余 假期 leave vacation balance days",
 "expense.status":"报销 审批 expense reimbursement claim approved",
 "order.delivery":"订单 配送 order delivery window",
 "warranty.coverage":"保修 质保 warranty coverage device serial",
 "subscription.renewal":"订阅 续费 subscription renewal renew",
 "utility.bill":"账单 水电 费用 utility bill account due",
 "currency.reference":"汇率 外汇 牌价 换汇 美元 美金 人民币 货币兑换 currency exchange rate forex fx dollar yuan renminbi euro cny rmb usd eur gbp jpy hkd aud cad chf sgd krw",
 "postcode.address":"邮编 邮政 postcode postal code address zip",
 "timezone.offset":"时区 时差 时间 timezone utc offset time difference",
 "holiday.calendar":"节假日 放假 holiday public calendar",
 "word.definition":"单词 词义 意思 word definition meaning dictionary",
 "name.pronunciation":"人名 发音 读音 pronunciation name ipa",
 "food.allergen":"过敏 食品 成分 allergen allergy food ingredients",
 "device.port":"设备 接口 电脑 端口 usb hdmi device port laptop",
 "air.quality":"空气 污染 空气质量 air quality pollution aqi",
 "pollen.level":"花粉 过敏 pollen level allergy",
 "uv.index":"紫外线 防晒 uv ultraviolet index sunscreen",
 "river.level":"河流 水位 river water level flood station",
 "beach.tide":"海滩 潮汐 涨潮 tide beach high tide",
 "noise.measurement":"噪声 分贝 noise decibel measurement",
 "recycling.category":"回收 分类 recycling category waste sorting",
 "alert.bulletin":"预警 灾害 公告 警报 alert bulletin warning disaster",
 "venue.accessibility":"无障碍 电梯 坡道 accessibility wheelchair elevator ramp",
 "wifi.access":"无线 网络 wifi internet access ssid",
 "lostfound.hours":"失物 招领 lost found office hours",
 "restroom.facility":"厕所 卫生间 洗手间 restroom toilet bathroom location",
 "luggage.capacity":"行李 寄存 箱 luggage baggage lockers storage",
 "ferry.operation":"轮渡 渡轮 码头 ferry terminal operation",
 "bike.docks":"单车 停放 车桩 bike bicycle docks empty",
 "canteen.menu":"食堂 菜单 午餐 canteen cafeteria menu lunch meal"
}
INTENTS.update({
    'stock.quote':'股票 股价 证券 现价 行情 个股 腾讯 茅台 苹果 宁德时代 stock share ticker quote price security equity ETF AAPL Tencent',
    'market.indices':'大盘 指数 上证 深证 创业板 沪深 恒生 纳斯达克 道琼斯 标普 market indices index board benchmark stocks market performance',
    'currency.trend':'汇率 走势 趋势 历史 曲线 最近 一周 一月 月份 美元 人民币 USD CNY currency exchange forex fx trend history chart past week month year',
})
INTENTS.update({
    'calendar.create':'添加 日程 新建 创建 安排 会议 add create schedule calendar event appointment',
    'calendar.update':'修改 日程 改期 移到 时间 update reschedule calendar event',
    'calendar.delete':'删除 日程 取消 移除 delete cancel remove calendar event',
    'calendar.restore':'恢复 日程 撤销 restore undo calendar event',
    'task.list':'待办 清单 查看 事项 任务 todos task list pending completed',
    'task.create':'添加 待办 新建 创建 事项 add create task todo',
    'task.update':'完成 待办 修改 勾选 事项 complete update task todo',
    'task.delete':'删除 待办 移除 事项 delete remove task todo',
    'task.restore':'恢复 待办 撤销 事项 restore undo task todo',
})


# Function words must not act as capability evidence. In the recorded user
# session, the date suffix “日的” ranked museum/sports above the requested
# weather tool and revoked the previously selected currency schema.
STOP_TERMS = frozenset("""
the a an and or is are am be to for of in on at it this that me my please
what when where how can could would will do does i you your get find check
日的 月的 年的 天的 某日 指定 查询 一下 查一 帮我 我想 想查 想要 请问 请帮
帮忙 一查 的情 情况 这个 那个 这次 那次 能否 可以 能不 不能 有没有
""".split())


def terms(text):
    text = text.lower()
    words = re.findall(r"[a-z0-9_]+", text)
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        words.extend(part[i:i+2] for i in range(len(part)-1))
        if len(part) == 1:
            words.append(part)
    return set(words) - STOP_TERMS


def no_hit_error(query, harness_available=False):
    temporal = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|年|月|日|号|今天|明天|昨天|today|tomorrow|yesterday", query, re.I)
    if temporal:
        return {"code":"incomplete_query", "message":"No tool capability was identified. Include the capability (for example the kind of information), not only a date. Previously visible schemas remain available."}
    if harness_available:
        return {"code": "no_matching_tool", "fallback_tool": HARNESS_TOOL,
                "message": "No matching tool was found. If the task still needs a tool, call assistant.query with the complete user request in arguments.task."}
    return {"code":"no_matching_tool", "message":"No matching tool capability was found. Answer without tools if possible, otherwise clarify the requested capability. Previously visible schemas remain available."}


def schema_documentation(schema):
    """Documentation-only overlay: tool/version/types/required fields unchanged."""
    if schema["name"] == "currency.reference":
        schema["description"] += "。base是原币、quote是目标币；返回rate表示1单位base可兑换的quote数量。使用ISO货币代码和用户要求日期，不要自行编造年份。仅为日级参考价，实际发布日期见reference_context.rate_date。"
        props=schema["parameters"]["properties"]
        props["base"]["description"]="原币ISO 4217三字母代码，例如CNY=人民币、USD=美元。人民币兑美元时base=CNY。"
        props["quote"]["description"]="目标币ISO 4217三字母代码。人民币兑美元时quote=USD；不要颠倒兑换方向。"
        props["date"]["description"]="YYYY-MM-DD。优先使用用户明确日期；相对日期使用会话时钟，缺年份不要默认为2024；不支持尚未公布的未来参考价。"
        schema["returns"]["properties"]["rate"]["description"]="1单位base对应的quote数量；不是1单位quote对应base。实际参考日期见payload.reference_context.rate_date，可能早于请求日。"
    return personal_documentation(schema)


# Owner-supplied tools: one JSON file per tool in backend/tools/custom/ (or the
# directory named by DUPLEX_CUSTOM_TOOLS), in the registry's own schema format
# plus an endpoint that receives the arguments. The committed registry and its
# checksum stay as they are; these are added next to it.
CUSTOM_DIR_ENV = "DUPLEX_CUSTOM_TOOLS"
CUSTOM_SOURCE = "custom_endpoint"
CUSTOM_KEYS = frozenset({"name", "description", "parameters", "returns", "endpoint", "read_only",
                         "domain", "excluded", "version", "example", "timeout_s"})
CUSTOM_NAME = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}")
SCHEMA_TYPES = frozenset({"object", "string", "integer", "number", "boolean", "array", "null"})
# Exactly what validate() enforces, plus documentation keys it ignores.
SCHEMA_FIELDS = frozenset({"type", "description", "properties", "required", "additionalProperties", "items",
                           "enum", "minimum", "maximum", "minLength", "maxLength", "multiline", "title", "examples"})


def custom_tool_dir():
    return Path(os.environ.get(CUSTOM_DIR_ENV) or ROOT / "custom")


def check_schema(schema, path):
    """Reject what validate() would not enforce, so a schema never promises more than is checked."""
    def bad(message):
        raise ToolError("custom_tool", path + " " + message)
    if type(schema) is not dict:
        bad("must be a JSON schema object")
    kind = schema.get("type")
    if kind not in SCHEMA_TYPES:
        bad("needs a type: " + ", ".join(sorted(SCHEMA_TYPES)))
    unknown = set(schema) - SCHEMA_FIELDS
    if unknown:
        bad("uses unsupported keywords: " + ", ".join(sorted(unknown)) + " (supported: " + ", ".join(sorted(SCHEMA_FIELDS)) + ")")
    if "enum" in schema and (type(schema["enum"]) is not list or not schema["enum"]):
        bad("enum must be a non-empty list")
    if kind == "object":
        props = schema.get("properties", {})
        if type(props) is not dict:
            bad("properties must be an object")
        for key, value in props.items():
            check_schema(value, f"{path}.properties.{key}")
        required = schema.get("required", [])
        if type(required) is not list or any(type(r) is not str or r not in props for r in required):
            bad("required must list names from properties")
        if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
            bad("additionalProperties must be true or false")
    elif "properties" in schema or "required" in schema:
        bad("has properties but is not an object")
    if kind == "array":
        if "items" not in schema:
            bad("is an array and needs items")
        check_schema(schema["items"], path + ".items")


def custom_entry(e, reserved):
    def bad(message):
        raise ToolError("custom_tool", message)
    if type(e) is not dict:
        bad("a tool file holds one JSON object")
    unknown = set(e) - CUSTOM_KEYS
    if unknown:
        bad("unknown fields: " + ", ".join(sorted(unknown)) + " (allowed: " + ", ".join(sorted(CUSTOM_KEYS)) + ")")
    name = e.get("name")
    if type(name) is not str or len(name) > 64 or not CUSTOM_NAME.fullmatch(name):
        bad('name must be lowercase words joined by dots, such as "home.lights"')
    if name in reserved:
        bad(f'"{name}" is already the name of a tool')
    if type(e.get("description")) is not str or not e["description"].strip() or len(e["description"]) > 600:
        bad("description must be a sentence of at most 600 characters")
    for key in ("parameters", "returns"):
        if key not in e:
            bad(key + " is required")
        check_schema(e[key], key)
        if e[key]["type"] != "object":
            bad(key + " must be an object schema")
    read_only = e.get("read_only", True)
    if type(read_only) is not bool:
        bad("read_only must be true or false")
    endpoint = e.get("endpoint")
    if endpoint is not None:
        parsed = urlsplit(endpoint) if type(endpoint) is str else None
        if (parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            bad("endpoint must be an http(s) URL without credentials, such as http://127.0.0.1:8765/lights")
    example = e.get("example", {})
    if type(example) is not dict or set(example) - {"arguments", "result"}:
        bad('example must be {"arguments": {...}, "result": {...}}')
    if "arguments" in example:
        validate(example["arguments"], e["parameters"], "example.arguments")
    if "result" in example:
        validate(example["result"], e["returns"], "example.result")
    if endpoint is None and "result" not in example:
        bad("give an endpoint, or an example result to answer with")
    timeout = e.get("timeout_s", 10)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 30:
        bad("timeout_s must be between 1 and 30 seconds")
    domain, version, excluded = e.get("domain", "custom"), e.get("version", "1"), e.get("excluded", "")
    if type(domain) is not str or not 0 < len(domain) <= 32:
        bad("domain must be a short label")
    if type(version) not in (str, int) or type(excluded) is not str or len(excluded) > 300:
        bad("version must be a string; excluded at most 300 characters")
    return {"name": name, "family": name, "domain": domain, "version": str(version),
            "description": e["description"], "excluded": excluded, "read_only": read_only,
            "parameters": copy.deepcopy(e["parameters"]), "returns": copy.deepcopy(e["returns"]),
            "split": "runtime", "requires_session_authorization": not read_only,
            "example": {"arguments": copy.deepcopy(example.get("arguments", {})),
                        **({"result": copy.deepcopy(example["result"])} if "result" in example else {})},
            "source": "custom", "production_ready": False, "custom": True,
            "endpoint": endpoint, "timeout_s": timeout}


def load_custom_tools(directory, reserved):
    tools = []
    if not directory.is_dir():
        return tools
    for path in sorted(directory.glob("*.json")):
        try:
            try:
                raw = strict_json(path.read_text(encoding="utf-8"))
            except ToolError as error:
                raise ToolError("custom_tool", "is not valid JSON" if error.code == "invalid_json" else str(error)) from None
            tools.append(custom_entry(raw, reserved | {t["name"] for t in tools}))
        except ToolError as error:
            raise ToolError("custom_tool", f"Custom tool {path.name}: {error}") from None
    return tools


class Catalog:
    def __init__(self, path=None, custom_dir=None):
        self.path = Path(path or ROOT / "registry.json")
        self.registry = strict_json(self.path.read_text())
        builtin = {t["name"]: t for t in self.registry["tools"]}
        if (len(builtin) != len(self.registry['tools'])
                or not (FINANCE_TOOLS|PERSONAL_TOOLS) <= set(builtin)
                or any(t["read_only"] is not True for n,t in builtin.items() if n not in PERSONAL_TOOLS)):
            raise ToolError("registry_identity", "The committed registry must keep its tool families; its writes are restricted to local personal tools")
        # Exactly the original build_registry.py digest, including its JSON
        # separators; do not invent a new registry identity for unchanged tools.
        self.builtin_sha256 = hashlib.sha256(json.dumps(self.registry["tools"],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if self.registry.get("sha256") != self.builtin_sha256:
            raise ToolError("registry_checksum", "Committed registry checksum mismatch")
        self.custom_dir = Path(custom_dir) if custom_dir is not None else custom_tool_dir()
        self.custom = load_custom_tools(self.custom_dir, set(builtin))
        self.tools = {**builtin, **{t["name"]: t for t in self.custom}}
        # Without owner tools the identity is the committed one; with them it covers them too.
        self.sha256 = (self.builtin_sha256 if not self.custom else
                       hashlib.sha256((self.builtin_sha256 + canonical(self.custom)).encode()).hexdigest())
        self._terms = {name: terms(t["description"] + " " + name.replace(".", " ") + " " + INTENTS.get(name, "")) for name, t in self.tools.items()}
        self._idf = {w: math.log(1 + len(self.tools)/(1 + sum(w in v for v in self._terms.values()))) for doc in self._terms.values() for w in doc}

    def schema(self, name):
        if name not in self.tools:
            raise ToolError("unknown_tool", "Unknown tool name")
        return schema_documentation(copy.deepcopy({k:self.tools[name][k] for k in SCHEMA_KEYS}))

    def discover(self, query, top_k=3):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ToolError("invalid_query", "select query must be nonempty and at most 500 characters")
        if ' '.join(query.casefold().split()) in {'concrete tool capability','your query here','insert query here'}:
            raise ToolError('invalid_query','Discovery query copied a documentation placeholder. Build query from the current user request and requested capability. Do not copy documentation text. No business tool was searched or executed.')
        if type(top_k) is not int or not 1 <= top_k <= 8:
            raise ToolError("invalid_top_k", "top_k must be 1..8")
        q = terms(query)
        exact_name = query.strip().casefold()
        ranked = []
        for name, doc in self._terms.items():
            matched = sorted(q & doc)
            score = sum(self._idf[w] for w in matched) / math.sqrt(max(1, len(doc)))
            if score > 0:
                ranked.append({"name": name, "score": round(score, 6), "matched_terms": matched, "schema": self.schema(name)})
        return sorted(ranked, key=lambda x:(x["name"] != exact_name, -x["score"], x["name"]))[:top_k]

    def cards(self):
        return [{**self.schema(n), "domain":t["domain"], "split":t["split"],
                 "requires_session_authorization":t.get("requires_session_authorization", False),
                 "runtime_mode":"local" if n in PERSONAL_TOOLS else "live" if n in LIVE_TOOLS else "custom" if t.get("endpoint") else "fixture",
                 "custom":t.get("custom", False),
                 "example_arguments":copy.deepcopy(t["example"]["arguments"])}
                for n,t in self.tools.items()]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ToolError("upstream_redirect", "Unexpected tool endpoint redirect")


FINANCE_TOOLS = frozenset({'stock.quote','market.indices','currency.trend'})
LIVE_TOOLS = {"timezone.offset", "weather.day", "sun.daylight", "currency.reference"} | FINANCE_TOOLS
ALLOWED_HOSTS = {"api.open-meteo.com", "geocoding-api.open-meteo.com", "api.frankfurter.dev",
                 'qt.gtimg.cn','smartbox.gtimg.cn','web.ifzq.gtimg.cn'}
CITY_ZONES = {
 "杭州":"Asia/Shanghai", "北京":"Asia/Shanghai", "上海":"Asia/Shanghai", "深圳":"Asia/Shanghai",
 "广州":"Asia/Shanghai", "成都":"Asia/Shanghai", "南京":"Asia/Shanghai", "武汉":"Asia/Shanghai",
 "中国":"Asia/Shanghai", "东京":"Asia/Tokyo", "首尔":"Asia/Seoul", "伦敦":"Europe/London",
 "巴黎":"Europe/Paris", "柏林":"Europe/Berlin", "纽约":"America/New_York", "洛杉矶":"America/Los_Angeles",
 "悉尼":"Australia/Sydney", "新加坡":"Asia/Singapore", "hangzhou":"Asia/Shanghai",
 "beijing":"Asia/Shanghai", "shanghai":"Asia/Shanghai", "shenzhen":"Asia/Shanghai", "london":"Europe/London",
 "new york":"America/New_York", "los angeles":"America/Los_Angeles", "tokyo":"Asia/Tokyo", "paris":"Europe/Paris",
 "berlin":"Europe/Berlin", "sydney":"Australia/Sydney", "singapore":"Asia/Singapore", "utc":"UTC"
}


def fetch_text(url, encoding='utf-8', timeout=6):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS
            or parsed.port not in (None,443) or parsed.username is not None or parsed.password is not None
            or parsed.fragment or not parsed.path.startswith('/')):
        raise ToolError("blocked_endpoint", "Tool endpoint is not allowlisted")
    req = Request(url, headers={"User-Agent":"Duopra/0.1"})
    # Tool-only transport override; leave the process/global urllib environment
    # untouched. Without the dedicated option, urllib keeps its system proxies.
    proxy=os.environ.get("DUPLEX_TOOL_HTTPS_PROXY", "").strip()
    handlers=[NoRedirect()]
    if proxy:handlers.append(ProxyHandler({"https":proxy}))
    with build_opener(*handlers).open(req, timeout=timeout) as r:
        data = r.read(512 * 1024 + 1)
    if len(data) > 512 * 1024:
        raise ToolError("upstream_size", "Tool result exceeds limit")
    return data.decode(encoding,'strict')


def get_json(host, path, args, timeout=4):
    if host not in ALLOWED_HOSTS or not path.startswith('/') or path.startswith('//'):
        raise ToolError('blocked_endpoint','Tool endpoint is not allowlisted')
    url = 'https://' + host + path + '?' + urlencode(args)
    return strict_json(fetch_text(url,timeout=timeout)), url


def live_execute(name, a):
    if name in FINANCE_TOOLS:
        try:
            return finance_execute(name,a,fetch=fetch_text)
        except FinanceError as error:
            raise ToolError(error.code,str(error)) from error
    if name == "timezone.offset":
        zone = CITY_ZONES.get(a["city"].strip().lower())
        if zone is None:
            raise ToolError("unknown_city", "City is not in the local IANA time-zone mapping")
        instant = datetime.fromisoformat(a["instant"].replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ToolError("ambiguous_instant", "instant must include Z or a UTC offset")
        offset = instant.astimezone(ZoneInfo(zone)).utcoffset()
        return {"offset_minutes":int(offset.total_seconds()//60)}, {"source":"local_iana_tzdb", "zone":zone}
    if name in {"weather.day", "sun.daylight"}:
        day = date.fromisoformat(a["date"])
        if not -5 <= (day - date.today()).days <= 15:
            raise ToolError("unsupported_date", "Live forecast supports the past five and next fifteen days")
        geo, geo_url = get_json("geocoding-api.open-meteo.com", "/v1/search", {"name":a["city"], "count":1, "language":"zh", "format":"json"})
        hits = geo.get("results") or []
        if not hits:
            raise ToolError("unknown_city", "No geocoding result")
        loc = hits[0]
        variables = "temperature_2m_mean,precipitation_probability_max" if name == "weather.day" else "sunrise,sunset"
        out, url = get_json("api.open-meteo.com", "/v1/forecast", {"latitude":loc["latitude"], "longitude":loc["longitude"], "timezone":"auto", "daily":variables, "start_date":day.isoformat(), "end_date":day.isoformat()})
        daily = out["daily"]
        if name == "weather.day":
            result = {"temperature_c":round(daily["temperature_2m_mean"][0]), "rain_probability":daily["precipitation_probability_max"][0] / 100}
        else:
            result = {"sunrise":daily["sunrise"][0].split("T")[1], "sunset":daily["sunset"][0].split("T")[1]}
        return result, {"source":"live_open_meteo", "urls":[geo_url,url], "resolved_city":loc["name"], "timezone":out["timezone"], "temperature_semantics":"rounded_daily_mean_celsius" if name=="weather.day" else None}
    if name == "currency.reference":
        base, quote = a["base"].upper(), a["quote"].upper()
        if not re.fullmatch(r"[A-Z]{3}",base) or not re.fullmatch(r"[A-Z]{3}",quote):
            raise ToolError("invalid_currency", "Currencies must have three alphabetic ISO codes")
        try:
            requested_day = date.fromisoformat(a["date"])
        except (TypeError,ValueError) as exc:
            raise ToolError("invalid_date", "currency.reference date must be YYYY-MM-DD") from exc
        today = datetime.now(timezone.utc).date()
        if requested_day > today:
            raise ToolError("reference_not_published", "No future reference rate is available for requested_date="+requested_day.isoformat()+"; current UTC date="+today.isoformat()+". Do not substitute latest for that requested date.")
        day = requested_day.isoformat()
        out, url = get_json("api.frankfurter.dev", "/v1/"+day, {"base":base,"symbols":quote})
        try:
            actual_day=date.fromisoformat(out["date"])
            rate=float(out["rates"][quote])
            response_base=out["base"]
        except (TypeError,ValueError,KeyError) as exc:
            raise ToolError("reference_unavailable", "Provider did not return a usable rate/date for requested_date="+day+", base="+base+", quote="+quote) from exc
        if response_base != base or not math.isfinite(rate) or rate <= 0 or actual_day > requested_day or actual_day > today:
            raise ToolError("invalid_reference_response", "Provider response does not match the requested currency direction or causal date")
        reference_context={"base":base,"quote":quote,"requested_date":day,"rate_date":actual_day.isoformat(),
                           "date_exact_match":actual_day==requested_day,"rate_semantics":"quote units per 1 base",
                           "not_transaction_quote":True,"reference_frequency":"daily; earlier working-day rate may be returned"}
        return {"rate":rate}, {"source":"live_frankfurter_reference", "url":url, **reference_context}
    raise ToolError("not_live", "No live adapter for this tool")


def custom_execute(tool, arguments):
    """POST the arguments to the owner's endpoint; one JSON object comes back."""
    request = Request(tool["endpoint"], data=canonical(arguments).encode(), method="POST",
                      headers={"Content-Type": "application/json", "Accept": "application/json",
                               "User-Agent": "Duopra/0.1", "X-Duopra-Tool": tool["name"],
                               "Idempotency-Key": secrets.token_hex(16)})
    try:
        # Direct connection (the endpoint is usually local) and no redirects.
        with build_opener(NoRedirect(), ProxyHandler({})).open(request, timeout=tool["timeout_s"]) as response:
            data = response.read(512 * 1024 + 1)
    except ToolError:
        raise
    except Exception as exc:
        status = getattr(exc, "code", None)
        raise ToolError("custom_unavailable", f"Custom tool endpoint failed" + (f" with HTTP {status}" if status else f": {type(exc).__name__}")) from exc
    if len(data) > 512 * 1024:
        raise ToolError("upstream_size", "Tool result exceeds limit")
    try:
        result = strict_json(data.decode("utf-8"))
    except (ToolError, UnicodeDecodeError) as exc:
        raise ToolError("custom_response", "Custom tool endpoint did not return one JSON object") from exc
    if type(result) is not dict:
        raise ToolError("custom_response", "Custom tool endpoint must return a JSON object")
    parsed = urlsplit(tool["endpoint"])
    return result, {"source": CUSTOM_SOURCE, "tool": tool["name"], "endpoint": f"{parsed.scheme}://{parsed.netloc}{parsed.path}"}


class Executor:
    def __init__(self, catalog=None, mode="auto", allow_fixture=True, personal_scope=None):
        if mode not in {"auto","live","fixture"}:
            raise ValueError("mode must be auto, live or fixture")
        self.catalog = catalog or Catalog()
        self.mode, self.allow_fixture = mode, allow_fixture
        self.personal_scope=personal_scope
        self.mutation_keys={}
        self.mutation_lock=threading.Lock()

    def execute(self, name, arguments, *, personal_authorized=False):
        if name not in self.catalog.tools:
            raise ToolError("unknown_tool", "Unknown tool name")
        tool = self.catalog.tools[name]
        validate(arguments, tool["parameters"])
        if tool.get("requires_session_authorization") and not personal_authorized:
            raise ToolError("authorization_required", "Personal sandbox tools require explicit session authorization")
        started = time.monotonic()
        if name in PERSONAL_TOOLS:
            if self.mode=='fixture':
                raise ToolError('fixture_disabled','Personal tools use a persistent local workspace, not fixed fixtures')
            request_key=None
            if not tool['read_only']:
                request_key=secrets.token_hex(16)
            if name.endswith('.create'):
                # Identical creates may be retries. Updates/deletes/restores
                # need fresh identities so true→false→true does not replay an
                # old result instead of applying the user's latest intent.
                fingerprint=canonical([name,arguments])
                with self.mutation_lock:
                    now=time.monotonic()
                    self.mutation_keys={k:v for k,v in self.mutation_keys.items() if now-v[0]<120}
                    if fingerprint not in self.mutation_keys:self.mutation_keys[fingerprint]=(now,secrets.token_hex(16))
                    request_key=self.mutation_keys[fingerprint][1]
            try:
                result=personal_execute(self.personal_scope,name,arguments,personal_authorized,request_key)
                validate(result,tool['returns'],'result')
            except PersonalError as error:
                raise ToolError(error.code,str(error)) from None
            return {'registry_sha256':self.catalog.sha256,'result':result,'source':'local_personal_store',
                    'is_fixture':False,'elapsed_ms':round((time.monotonic()-started)*1000),
                    'provenance':{'workspace':'browser_private_local','sync':'local_only','persistent':True}}
        if name in FINANCE_TOOLS and self.mode == 'fixture':
            raise ToolError('fixture_disabled','Finance tools require an actual live source')
        if self.mode != "fixture" and name in LIVE_TOOLS:
            try:
                result, provenance = live_execute(name,arguments)
                validate(result,tool["returns"],"result")
                return {"registry_sha256":self.catalog.sha256,"result":result,"source":provenance["source"],"provenance":provenance,"elapsed_ms":round((time.monotonic()-started)*1000),"is_fixture":False}
            except ToolError:
                # Preserve actionable date/currency failures instead of
                # collapsing them all into an apparent network outage.
                raise
            except Exception as exc:
                # A live-capable tool does NOT silently turn into simulated facts.
                raise ToolError("live_unavailable", "Live tool unavailable: "+str(exc)[:200]) from exc
        if tool.get("custom") and tool.get("endpoint") and self.mode != "fixture":
            result, provenance = custom_execute(tool, arguments)
            validate(result, tool["returns"], "result")
            return {"registry_sha256":self.catalog.sha256,"result":result,"source":CUSTOM_SOURCE,"provenance":provenance,
                    "elapsed_ms":round((time.monotonic()-started)*1000),"is_fixture":False}
        if self.mode == "live" or not self.allow_fixture:
            raise ToolError("fixture_disabled", "No live adapter is configured; sandbox fixture is disabled")
        if "result" not in tool["example"]:
            raise ToolError("fixture_disabled", "This tool has no example result to answer with")
        result = copy.deepcopy(tool["example"]["result"])
        validate(result,tool["returns"],"result")
        key = hashlib.sha256((name+canonical(arguments)).encode()).hexdigest()
        return {"fixture_key":key,"registry_sha256":self.catalog.sha256,"result":result,
                "source":"synthetic_sandbox_not_live_api","is_fixture":True,
                "provenance":{"notice":"演示夹具：返回值是固定模拟结果，不能用于真实决策；参数仅用于验证调用格式。","arguments":copy.deepcopy(arguments)},
                "elapsed_ms":round((time.monotonic()-started)*1000)}


def backend_text(value):
    # Generated/remote strings cannot escape the private backend wrapper.
    return "<backend>"+canonical(value).replace("<","\\u003c").replace(">","\\u003e")+"</backend>"


def model_backend_value(value):
    """Match SFT observation shape; retain rich provenance only in UI events."""
    result=copy.deepcopy(value)
    if isinstance(result,dict)and isinstance(result.get("payload"),dict):
        payload=result["payload"]
        result["payload"]={k:payload[k]for k in ("fixture_key","registry_sha256","result","source","response_for_arguments")if k in payload}
        if payload.get("source")=="live_frankfurter_reference":
            reference=payload.get("provenance",{})
            result["payload"]["reference_context"]={k:reference[k]for k in
                ("base","quote","requested_date","rate_date","date_exact_match","rate_semantics","not_transaction_quote","reference_frequency")if k in reference}
    return result


class ToolSession:
    """Session-local authority and causally ordered input-unit delivery queue.

    Use on the model-owner loop. No operation calls an LLM or speech decoder.
    Futures only perform read-only tools. before_input_unit is nonblocking.
    """
    def __init__(self, session_id, catalog=None, *, executor=None, top_k=3, initial_tools=(), max_pending=4, max_calls=128, discovery_mode="legacy", fixed_meta_schemas=()):
        self.catalog = catalog or Catalog()
        if discovery_mode not in ("legacy","function"):raise ToolError("discovery_mode","Unknown discovery mode")
        self.discovery_mode=discovery_mode
        self.executor = executor or Executor(self.catalog)
        self.session_id = str(session_id)
        self.top_k,self.max_pending,self.max_calls = top_k,max_pending,max_calls
        self.pool = ThreadPoolExecutor(max_workers=max_pending,thread_name_prefix="venus-readonly-tool")
        self.generation = 0
        self.unit_index = -1
        self.personal_authorized = False
        self.hot_tools = set(initial_tools)
        self.discovered_tools = set()
        self.fixed_meta_schemas = {s['name']:copy.deepcopy(s) for s in fixed_meta_schemas}
        self.visible = set(initial_tools) | set(self.fixed_meta_schemas)
        for n in self.visible:self.catalog.schema(n)
        self.pending = {}
        # Model call_id is a correlation label, not an idempotency key. Each
        # accepted action gets a separate host-only identity, including retries
        # with the same label/arguments. Never key pending futures by call_id.
        self.execution_count = 0
        self.outbox = []
        self.format_rejections = 0
        self.events = []
        self.event_seq = 0
        self.closed = False
        self._event("session_open", tools=sorted(self.visible))

    def _event(self, kind, **data):
        self.event_seq += 1
        event={"seq":self.event_seq,"type":kind,"session_id":self.session_id,"generation":self.generation,"unit_index":self.unit_index,"at_ms":time.time_ns()//1000000,**copy.deepcopy(data)}
        self.events.append(event)
        self.events=self.events[-256:]
        return event

    def events_since(self, sequence=0):
        return copy.deepcopy([e for e in self.events if e["seq"]>sequence])

    def snapshot(self):
        return {"schema_version_policy":SCHEMA_VERSION_POLICY,"schema_version_required":False,"discovery_mode":self.discovery_mode,"meta_tools":([discovery_schema()]if self.discovery_mode=="function"else[])+list(self.fixed_meta_schemas.values()),"generation":self.generation,"unit_index":self.unit_index,"hot_tools":[self.catalog.schema(n) for n in sorted(self.hot_tools)],"discovered_tools":[self.catalog.schema(n) for n in sorted(self.discovered_tools)],"visible_tools":[self.catalog.schema(n) for n in sorted(self.visible)],"pending":[{"execution_id":k,"call_id":v["action"]["call_id"],"op":v["action"]["op"],"tool":v["action"].get("tool"),"submitted_unit":v["submitted_unit"]} for k,v in self.pending.items()],"personal_authorized":self.personal_authorized,"registry_sha256":self.catalog.sha256,"executor_mode":self.executor.mode}

    def authorize_personal(self, allowed):
        if type(allowed) is not bool:raise ToolError("invalid_authorization","authorization must be boolean")
        self.personal_authorized=allowed
        self._event("authorization",personal_authorized=allowed)
        # Only explicit session opt-in is authoritative; speech/model text
        # never grants access. Coalesce not-yet-delivered factual updates.
        self.outbox=[item for item in self.outbox if item['kind']!='authorization']
        self.outbox.append({'generation':self.generation,'submitted_unit':self.unit_index,
            'value':{'personal_authorized':allowed,'authorization_source':'explicit_session_setting'},
            'kind':'authorization','origin':'model'})

    def expose(self, names, *, origin="toolbar"):
        if type(names)is not list or len(names)>8:raise ToolError("invalid_tool_selection","Choose zero to eight tools")
        schemas=[self.catalog.schema(n) for n in dict.fromkeys(names)]
        # Only the latest toolbar selection for the next audio unit is visible.
        self.outbox=[item for item in self.outbox if item["kind"]!="expose"]
        if schemas or self.unit_index>=0 or self.discovery_mode=="function":
            self.outbox.append({"generation":self.generation,"submitted_unit":self.unit_index,"value":{"tools":schemas},"kind":"expose","origin":origin})
        self._event("tools_exposed",origin=origin,tools=[s["name"]for s in schemas])
        return schemas

    def submit_private(self, raw, unit_index=None, *, origin="model"):
        if self.closed:raise ToolError("closed","Tool session is closed")
        if origin not in {"model","manual"}:raise ToolError("invalid_origin","Unknown action origin")
        if unit_index is not None and unit_index!=self.unit_index:
            raise ToolError("unit_mismatch","Action must belong to the current model unit")
        if len(raw)>8192:raise ToolError("action_too_large","Private JSON exceeds 8192 characters")
        action=strict_json(raw)
        if type(action)is not dict:raise ToolError("invalid_action","Private action must be a JSON object")
        # Unambiguous discovery-only compatibility. Never guess arguments for a
        # business call, repair a missing tool name, or join separate actions.
        original_fields=sorted(action)
        action,personal_compatibility=normalize_personal_action(action,self.catalog.tools)
        if personal_compatibility:
            self._event('protocol_compatibility',origin=origin,
                call_id=action.get('call_id'),original_fields=original_fields,
                **personal_compatibility)
        # A missing correlation label does not make tool/arguments ambiguous.
        # Supply only this host metadata; never infer op, tool or parameters.
        host_call_id = action.get('op') in ('call', 'select') and 'call_id' not in action
        if host_call_id:
            action = dict(action, call_id=f'rt-{self.generation}-{self.execution_count + 1}')
        discovery_flat=(action.get('op')=='call' and action.get('tool')==DISCOVERY_NAME and
                        'arguments'not in action and 'query'in action and
                        not set(action)-{'op','call_id','tool','schema_version','query','top_k'})
        if discovery_flat:
            action=dict(action)
            args={'query':action.pop('query')}
            if 'top_k'in action:args['top_k']=action.pop('top_k')
            action['arguments']=args
        normalized_fields=sorted(action)
        op=action.get("op")
        if not isinstance(op,str):
            raise ToolError("action_fields", "Private action op must be the string select or call")
        expected={"select":{"op","call_id","query"},"call":{"op","call_id","tool","arguments"}}.get(op)
        if expected is None:
            raise ToolError("action_fields", "Private action op must be select or call. select fields: op,call_id,query. call required fields: op,call_id,tool,arguments; schema_version is optional metadata.")
        optional={"schema_version"} if op=="call" else set()
        if not expected.issubset(action) or set(action)-(expected|optional):
            raise ToolError("action_fields", "Private action fields invalid; missing="+canonical(sorted(expected-set(action)))+"; unexpected="+canonical(sorted(set(action)-(expected|optional)))+". Required "+str(op)+" fields="+canonical(sorted(expected))+"; optional="+canonical(sorted(optional))+". JSON field order is unrestricted.")
        discovery_entrypoint="select"
        discovery_top_k=self.top_k
        version_metadata={}
        if op=="call" and action.get("tool")==DISCOVERY_NAME:
            if self.discovery_mode!="function":raise ToolError("schema_not_visible","Discovery function is not enabled for this session")
            version_metadata=schema_version_metadata(action,DISCOVERY_VERSION)
            validate(action["arguments"],discovery_schema()["parameters"])
            discovery_top_k=action["arguments"].get("top_k",self.top_k)
            action={"op":"select","call_id":action["call_id"],"query":action["arguments"]["query"]}
            op="select";discovery_entrypoint="function_call"
        call_id=action["call_id"]
        if not isinstance(call_id,str)or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}",call_id):raise ToolError("invalid_call_id","call_id must be a short identifier")
        if self.execution_count>=self.max_calls:raise ToolError("call_budget","Session tool action budget exhausted")
        if len(self.pending)>=self.max_pending:raise ToolError("tool_queue_full","Pending tool action limit reached")
        if discovery_flat:
            self._event('protocol_compatibility',call_id=call_id,origin=origin,
                        adapter='discovery_outer_query_to_arguments',original_fields=original_fields,
                        normalized_fields=normalized_fields)
        if host_call_id:
            self._event('protocol_compatibility',call_id=call_id,origin=origin,
                        adapter='host_allocated_correlation_id',original_fields=original_fields,
                        normalized_fields=normalized_fields,business_arguments_changed=False)
        execution_seq=self.execution_count+1
        execution_id="exec-"+str(execution_seq)
        if op=="select":
            hits=self.catalog.discover(action["query"],discovery_top_k)
            value={"call_id":call_id,"tools":[h["schema"]for h in hits]}
            if not hits:value["error"]=no_hit_error(action["query"], HARNESS_TOOL in self.fixed_meta_schemas)
            self.execution_count=execution_seq
            self.outbox.append({"execution_id":execution_id,"execution_seq":execution_seq,"generation":self.generation,"submitted_unit":self.unit_index,"value":value,"kind":"select","update_visible":bool(hits),"origin":origin})
            self._event("selected",execution_id=execution_id,call_id=call_id,query=action["query"],candidates=hits,
                        visible_policy="merge_with_hot_at_delivery" if hits else "retain_existing_no_capability_match",error=value.get("error"),origin=origin,
                        discovery_entrypoint=discovery_entrypoint,meta_tool=DISCOVERY_NAME if discovery_entrypoint=="function_call"else None,requested_top_k=discovery_top_k,**version_metadata)
        else:
            name=action["tool"]
            if type(name)is str and name not in self.catalog.tools:
                raise ToolError('unknown_tool','This tool name is not in the actual catalog. Do not invent names. Use tools.discover or select with the concrete capability, wait for the returned schema, then call that exact name.')
            if type(name)is not str or name not in self.visible:
                discovery = DISCOVERY_NAME if self.discovery_mode == "function" else "select"
                raise ToolError("schema_not_visible", "Tool was not executed. Use " + discovery + " to get its schema before calling it.")
            version_metadata=schema_version_metadata(action,self.catalog.tools[name]["version"])
            validate(action["arguments"],self.catalog.tools[name]["parameters"])
            if self.catalog.tools[name].get("requires_session_authorization")and not self.personal_authorized:raise ToolError("authorization_required","Personal tools require explicit session authorization")
            invalid_patch=personal_patch_error(name,action['arguments'])
            if invalid_patch:raise ToolError(*invalid_patch)
            future=self.pool.submit(self.executor.execute,name,copy.deepcopy(action["arguments"]),personal_authorized=self.personal_authorized)
            self.execution_count=execution_seq
            self.pending[execution_id]={"execution_seq":execution_seq,"future":future,"generation":self.generation,"submitted_unit":self.unit_index,"action":copy.deepcopy(action),"origin":origin}
            self._event("submitted",execution_id=execution_id,call_id=call_id,tool=name,arguments=action["arguments"],origin=origin,**version_metadata)
        return {"accepted":True,"execution_id":execution_id,"call_id":call_id,"generation":self.generation,"earliest_delivery_unit":self.unit_index+1}

    def begin_user_speech(self):
        self.format_rejections = 0
        self.outbox = [item for item in self.outbox if item["kind"] != "error" or
                       item["value"]["error"]["code"] not in CALL_FORMAT_ERRORS]

    def report_error(self, error, *, origin="model", action=None, turn_quarantined=False):
        code=error.code if isinstance(error,ToolError)else "invalid_private_action"
        stop_retrying = False
        if origin == 'model' and code in CALL_FORMAT_ERRORS:
            if self.format_rejections > MAX_FORMAT_RETRIES:
                return
            self.format_rejections += 1
            stop_retrying = self.format_rejections > MAX_FORMAT_RETRIES
        # A rejected retry never cancels, replaces, or executes an earlier valid
        # call carrying the same model label. Correlate the rejection so the
        # model does not mistake it for failure of that accepted pending call.
        call_id=action.get("call_id") if isinstance(action,dict) else None
        if not isinstance(call_id,str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}",call_id):call_id=None
        pending=[{"execution_id":execution_id,"tool":p["action"].get("tool"),"submitted_unit":p["submitted_unit"],"status":"accepted_awaiting_result_delivery"}
                 for execution_id,p in self.pending.items() if p["generation"]==self.generation and p["origin"]==origin and p["action"]["call_id"]==call_id]
        value={"error":{"code":code,"message":str(error)[:300]}}
        if code=='action_fields':
            value['error']['message'] = str(error)[:400] + (
                ' For op=call, put tool and optional schema_version at the top level, alongside call_id. '
                'The arguments object contains only fields from the tool schema.'
            )
        if code=='authorization_required':
            value['error']['message']='Personal tools are not authorized. Ask the user to enable local personal tools; do not repeat the rejected call until authorized.'
        if call_id is not None:value["call_id"]=call_id
        if pending:
            value["error"]["accepted_pending_calls"]=pending
            value["error"]["retry_rejection_did_not_cancel_pending"]=True
        raw_tool=action.get("tool") if isinstance(action,dict) else None
        requested_tool=raw_tool if isinstance(raw_tool,str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,95}",raw_tool) else None
        arguments=action.get('arguments') if isinstance(action,dict) else None
        agenda_write_fields = (
            code == 'unknown_argument' and requested_tool == 'calendar.agenda'
            and isinstance(arguments, dict) and bool({'title', 'starts', 'start', 'end'} & arguments.keys())
        )
        discovery_target = requested_tool if code == 'schema_not_visible' else None
        if agenda_write_fields:
            value['error']['message'] += (
                ' calendar.agenda is read-only. Its arguments are calendar_id and date. '
                'If the user asked to create an event, use calendar.create with title, start and end.'
            )
            if 'calendar.create' not in self.visible:
                discovery_target = 'calendar.create'
        if discovery_target in self.catalog.tools:
            if self.discovery_mode == 'function':
                discovery_action = {'op': 'call', 'tool': DISCOVERY_NAME, 'arguments': {'query': discovery_target}}
            else:
                discovery_action = {'op': 'select', 'query': discovery_target}
            value['error']['suggested_action'] = discovery_action
            value['error']['retry_guidance'] = (
                'Discover the tool using a fresh call ID, then use the returned schema to rebuild the call. '
                'Report success only after the tool result confirms it.'
            )
        if requested_tool in PERSONAL_TOOLS and not agenda_write_fields:
            value['call_contract']=personal_error_contract(requested_tool,self.catalog.schema(requested_tool))
        if code in PRIVATE_SPAN_ERRORS:
            value['error']['incomplete_action_executed']=False
            value['error']['accepted_pending_calls']=[
                {'execution_id':key,'call_id':p['action']['call_id'],
                 'tool':p['action'].get('tool'),'status':'accepted_awaiting_result_delivery'}
                for key,p in self.pending.items()
                if p['generation']==self.generation and p['origin']==origin]
            value['error']['retry_guidance']='Do not repeat accepted calls. If still requested, submit one complete JSON object inside a closed delegate; use op=call for business tools. Never claim a write succeeded before its success result.'
        if stop_retrying:
            value['error']['stop_retrying'] = True
            value['error']['retry_guidance'] = (
                'Stop retrying this malformed action. After turn EOS, tell the user it was not completed. '
                'Wait for new user input before attempting it again.'
            )
        elif turn_quarantined:
            value['error']['wait_for_turn_eos'] = True
            value['error']['retry_guidance'] = (
                'End the current turn with turn EOS before retrying. Output is quarantined until EOS. '
                + value['error'].get('retry_guidance', '')
            )
        self._event("rejected",code=code,message=str(error),call_id=call_id,accepted_pending_calls=pending,origin=origin,
                    requested_tool=requested_tool,arguments_present=isinstance(action,dict) and "arguments" in action,
                    action_field_names=sorted(action) if isinstance(action,dict) else [],
                    submitted_op=action.get('op') if isinstance(action,dict) else None,
                    argument_field_names=sorted(action['arguments']) if isinstance(action,dict) and isinstance(action.get('arguments'),dict) else None,
                    visible_business_tools=sorted(self.visible),pinned_tool_names=sorted(self.hot_tools),
                    discovered_tool_names=sorted(self.discovered_tools),fixed_meta_tools=[DISCOVERY_NAME] if self.discovery_mode=="function" else [])
        self.outbox.append({"generation":self.generation,"submitted_unit":self.unit_index,"value":value,"kind":"error","origin":origin})

    def before_input_unit(self, unit_index):
        if type(unit_index)is not int or unit_index<=self.unit_index:raise ToolError("unit_sequence","Model input units must advance monotonically")
        self.unit_index=unit_index
        for execution_id,p in list(self.pending.items()):
            if not p["future"].done():continue
            del self.pending[execution_id]
            call_id=p["action"]["call_id"]
            if p["generation"]!=self.generation:
                self._event("stale_discarded",execution_id=execution_id,call_id=call_id)
                continue
            try:
                payload=p["future"].result()
                value={"call_id":call_id,"payload":payload}
                self._event("completed",execution_id=execution_id,call_id=call_id,tool=p["action"]["tool"],payload=payload,origin=p["origin"])
            except Exception as exc:
                value={"call_id":call_id,"error":{"code":getattr(exc,"code","tool_failed"),"message":str(exc)[:300]}}
                self._event("failed",execution_id=execution_id,call_id=call_id,tool=p["action"]["tool"],error=value["error"],origin=p["origin"])
            self.outbox.append({"execution_id":execution_id,"execution_seq":p["execution_seq"],"generation":p["generation"],"submitted_unit":p["submitted_unit"],"value":value,"kind":"call","origin":p["origin"]})
        # Reused model labels cannot distinguish out-of-order completions.
        # Execute in parallel, but deliver the same(origin,call_id) in accepted
        # order. Unrelated labels and UI-only diagnostics remain independent.
        groups={}
        first_pending={}
        for p in self.pending.values():
            if p["generation"]==self.generation:
                lane=(p["origin"],p["action"]["call_id"])
                first_pending[lane]=min(first_pending.get(lane,p["execution_seq"]),p["execution_seq"])
        for item in self.outbox:
            if "execution_id"in item and item["generation"]==self.generation:
                lane=(item["origin"],item["value"]["call_id"])
                groups.setdefault(lane,[]).append(item)
        for items in groups.values():items.sort(key=lambda item:item["execution_seq"])
        ordered,handled=[],set()
        for item in self.outbox:
            if "execution_id"not in item or item["generation"]!=self.generation:
                ordered.append(item);continue
            lane=(item["origin"],item["value"]["call_id"])
            if lane not in handled:
                ordered.extend(groups[lane]);handled.add(lane)
        deliver,remaining=[],[]
        blocked=set()
        for item in ordered:
            if item["generation"]!=self.generation:continue
            lane=(item["origin"],item["value"].get("call_id"))
            ordered_action="execution_id"in item
            if (item["submitted_unit"]>=unit_index or ordered_action and
                    (lane in blocked or first_pending.get(lane,item["execution_seq"])<item["execution_seq"])):
                remaining.append(item)
                if ordered_action:blocked.add(lane)
                continue
            # Manual execution is a UI diagnostic and NEVER becomes model evidence.
            if item["origin"]=="manual":
                self._event("manual_result",execution_id=item.get("execution_id"),call_id=item["value"].get("call_id"),value=item["value"])
                continue
            if item["kind"]=="expose":
                self.hot_tools={s["name"] for s in item["value"].get("tools",[])}
            elif item["kind"]=="select" and item.get("update_visible",True):
                self.discovered_tools={s["name"] for s in item["value"].get("tools",[])}
            # A new discovery replaces only the dynamic pool, never the pinned
            # hotbar. Both sets take effect only when their schemas are delivered.
            self.visible=self.hot_tools | self.discovered_tools | set(self.fixed_meta_schemas)
            observation=discovery_observed(item["value"],self.discovery_mode=="function")
            if 'tools' in observation:
                observation['tools']=[s for s in observation['tools']if s['name']not in self.fixed_meta_schemas]+list(self.fixed_meta_schemas.values())
            if item['kind']=='expose':
                observation['personal_authorized']=self.personal_authorized
                observation['authorization_source']='explicit_session_setting'
                observation['private_action_protocol']={
                    'call_required_fields':['op','call_id','tool','arguments'],
                    'call_op':'call','arguments_type':'object','schema_version':'optional metadata',
                    'select_required_fields':['op','call_id','query'],'select_op':'select',
                    'rule':'Use one complete JSON object per delegate. Close each delegate before opening another. Every business tool uses op=call, including create/update/delete tools; the operation is in the tool name. Use visible names and required parameter keys. Do not split headers from arguments. Personal access requires user authorization.'}
                discovery = DISCOVERY_NAME if self.discovery_mode == 'function' else 'select'
                workflow = (f'没有匹配的可见工具时，先用 {discovery} 查找 Top-K 相关工具，'
                            '再按返回的 schema 调用匹配工具。收到成功结果后再确认完成。')
                if HARNESS_TOOL in self.fixed_meta_schemas:
                    workflow += '发现结果里没有匹配工具时，用 assistant.query 委派完整任务。'
                observation['private_action_protocol']['discovery_workflow'] = workflow
            deliver.append(backend_text(model_backend_value(observation)))
            self._event("injected",execution_id=item.get("execution_id"),call_id=item["value"].get("call_id"),action_kind=item["kind"],delivered_unit=unit_index,
                        schema_names=[s["name"] for s in observation.get("tools",[])],
                        business_schema_names=[s['name']for s in observation.get('tools',[])if s['name']!=DISCOVERY_NAME],
                        fixed_meta_schema_names=[s['name']for s in observation.get('tools',[])if s['name']==DISCOVERY_NAME],
                        discovery_error_code=item['value'].get('error',{}).get('code')if item['kind']=='select'else None,
                        visible_tool_names=sorted(self.visible),pinned_tool_names=sorted(self.hot_tools),discovered_tool_names=sorted(self.discovered_tools))
        self.outbox=remaining
        return deliver

    def cancel(self, reason="user_interrupt"):
        old=self.generation
        self.generation+=1
        for execution_id,p in self.pending.items():
            p["future"].cancel()
            self._event("cancelled",execution_id=execution_id,call_id=p["action"]["call_id"],reason=reason,previous_generation=old)
        self.pending.clear()
        self.outbox.clear()
        self._event("generation_cancelled",reason=reason,previous_generation=old)

    def close(self):
        if not self.closed:
            self.cancel("session_closed")
            self.closed=True
            self.pool.shutdown(wait=False,cancel_futures=True)


class PrivateStream:
    """Split tags over arbitrary text deltas; incomplete private JSON never runs.

    UI text filtering only: keep native model token+hidden TTS filtering enabled.
    feed returns visible text and complete private JSON strings. One action per
    model turn mirrors native Venus; finish_turn must follow actual turn_eos.
    """
    OPEN="<delegate>"
    CLOSE="</delegate>"
    def __init__(self,max_chars=8192):
        self.max_chars=max_chars
        self.reset()

    def reset(self):
        self.buffer=""
        self.private=False
        self.seen=False
        self.failed=False

    def feed(self,delta):
        if self.failed:return "",[]
        self.buffer+=delta
        visible,actions="",[]
        while self.buffer:
            tag=self.CLOSE if self.private else self.OPEN
            position=self.buffer.find(tag)
            if position<0:
                if self.private:
                    if len(self.buffer)>self.max_chars:
                        self.failed=True
                        raise ToolError("private_budget","Private span exceeds character budget")
                    break
                held=0
                for n in range(min(len(self.buffer),len(tag)-1),0,-1):
                    if self.buffer.endswith(tag[:n]):held=n;break
                stop=len(self.buffer)-held
                visible+=self.buffer[:stop]
                self.buffer=self.buffer[stop:]
                break
            part,self.buffer=self.buffer[:position],self.buffer[position+len(tag):]
            if self.private:
                if len(part)>self.max_chars:raise ToolError("private_budget","Private span exceeds character budget")
                # Validation at close, never on parseable prefixes.
                strict_json(part)
                actions.append(part)
                self.private=False
            else:
                visible+=part
                if self.seen:
                    self.failed=True
                    raise ToolError("multiple_private_actions","Only one private action is admitted per native model turn")
                self.private=True
                self.seen=True
        return visible,actions

    def finish_turn(self):
        incomplete=self.private or bool(self.buffer)
        self.reset()
        if incomplete:raise ToolError("unclosed_private_span","Turn ended with an incomplete delegate delimiter or private JSON")
