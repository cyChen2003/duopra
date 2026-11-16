/** Plain-language presentation of tool calls: short titles, field labels, units and
 * phrasing for every tool in the Duopra registry. Unknown tools and fields fall
 * back to readable defaults, so a registry change never breaks a card. */
import { getLanguage } from "./i18n.js?v=20261027-duopra-r9-personal";

const pick = (pair) => (getLanguage() === "zh" ? pair[0] : pair[1]);

const TITLES = {
  "weather.day": ["天气", "Weather"],
  "sun.daylight": ["日出日落", "Sunrise & sunset"],
  "rail.departures": ["列车班次", "Train departures"],
  "bus.next_arrival": ["公交到站", "Next bus"],
  "flight.status": ["航班状态", "Flight status"],
  "parking.vacancy": ["停车位", "Parking"],
  "charging.connector": ["充电桩", "EV charging"],
  "road.closure": ["道路封闭", "Road closure"],
  "store.hours": ["营业时间", "Store hours"],
  "stock.item": ["门店库存", "In-store stock"],
  "parcel.status": ["快递物流", "Parcel tracking"],
  "locker.hours": ["自提柜", "Parcel locker"],
  "waste.collection": ["垃圾清运", "Waste collection"],
  "water.outage": ["停水通知", "Water outage"],
  "power.outage": ["停电通知", "Power outage"],
  "laundry.machine": ["洗衣房", "Laundry"],
  "library.copy": ["图书馆藏", "Library copies"],
  "museum.exhibition": ["博物馆展览", "Museum exhibitions"],
  "cinema.showtime": ["电影场次", "Showtimes"],
  "theatre.performance": ["剧院演出", "Theatre"],
  "sports.fixture": ["赛程", "Fixtures"],
  "park.access": ["公园入口", "Park access"],
  "trail.status": ["步道状态", "Trail status"],
  "pool.lanes": ["泳道", "Pool lanes"],
  "calendar.agenda": ["日程", "Calendar"],
  "meeting.room": ["会议室", "Meeting room"],
  "leave.balance": ["年假余额", "Leave balance"],
  "expense.status": ["报销进度", "Expense claim"],
  "order.delivery": ["订单送达", "Order delivery"],
  "warranty.coverage": ["保修", "Warranty"],
  "subscription.renewal": ["订阅续费", "Subscription"],
  "utility.bill": ["账单", "Utility bill"],
  "currency.reference": ["汇率", "Exchange rate"],
  "postcode.address": ["邮政编码", "Postcode"],
  "timezone.offset": ["时区", "Time zone"],
  "holiday.calendar": ["公共假日", "Public holidays"],
  "word.definition": ["词典", "Dictionary"],
  "name.pronunciation": ["人名读音", "Pronunciation"],
  "food.allergen": ["过敏原", "Allergens"],
  "device.port": ["设备接口", "Device ports"],
  "air.quality": ["空气质量", "Air quality"],
  "pollen.level": ["花粉", "Pollen"],
  "uv.index": ["紫外线", "UV index"],
  "river.level": ["河流水位", "River level"],
  "beach.tide": ["潮汐", "Tides"],
  "noise.measurement": ["噪声", "Noise level"],
  "recycling.category": ["垃圾分类", "Recycling"],
  "alert.bulletin": ["灾害预警", "Hazard alerts"],
  "venue.accessibility": ["无障碍设施", "Accessibility"],
  "wifi.access": ["访客 Wi‑Fi", "Guest Wi‑Fi"],
  "lostfound.hours": ["失物招领", "Lost & found"],
  "restroom.facility": ["无障碍卫生间", "Accessible restroom"],
  "luggage.capacity": ["行李寄存", "Luggage storage"],
  "ferry.operation": ["渡轮", "Ferry service"],
  "bike.docks": ["共享单车桩", "Bike docks"],
  "canteen.menu": ["食堂菜单", "Canteen menu"],
  "stock.quote": ["股票行情", "Stock quote"],
  "market.indices": ["大盘指数", "Market indices"],
  "currency.trend": ["汇率走势", "FX trend"],
};

const FIELDS = {
  // parameters
  city: ["城市", "City"], date: ["日期", "Date"], origin: ["出发", "From"], destination: ["到达", "To"],
  stop: ["站点", "Stop"], route: ["线路", "Route"], flight: ["航班", "Flight"], parking_id: ["停车场", "Car park"],
  station_id: ["充电站", "Station"], connector: ["接口", "Connector"], road: ["道路", "Road"], store_id: ["门店", "Store"],
  sku: ["商品", "Item"], tracking_id: ["运单", "Tracking no."], locker_id: ["自提柜", "Locker"], community: ["社区", "Community"],
  waste_type: ["类别", "Type"], address: ["地址", "Address"], room: ["房间", "Room"], machine_type: ["设备", "Machine"],
  branch: ["分馆", "Branch"], isbn: ["ISBN", "ISBN"], museum: ["博物馆", "Museum"], cinema: ["影院", "Cinema"],
  movie: ["电影", "Film"], theatre: ["剧院", "Theatre"], team: ["球队", "Team"], park: ["公园", "Park"],
  entrance: ["入口", "Entrance"], trail: ["线路", "Trail"], pool: ["泳馆", "Pool"], time: ["时间", "Time"],
  calendar_id: ["日历", "Calendar"], year: ["年份", "Year"], claim_id: ["报销单", "Claim"], order_id: ["订单", "Order"],
  serial_number: ["序列号", "Serial no."], subscription_id: ["订阅", "Subscription"], account_id: ["账户", "Account"],
  period: ["账期", "Period"], base: ["基准货币", "Base"], quote: ["报价货币", "Quote"], district: ["区", "District"],
  street: ["街道", "Street"], instant: ["时刻", "Instant"], region: ["地区", "Region"], word: ["词语", "Word"],
  language: ["语言", "Language"], name: ["姓名", "Name"], model: ["型号", "Model"], station: ["站点", "Station"],
  beach: ["海滩", "Beach"], item: ["物品", "Item"], venue: ["场馆", "Venue"], office: ["服务点", "Office"],
  terminal: ["渡口", "Terminal"], canteen: ["食堂", "Canteen"], meal: ["餐次", "Meal"],
  // returns
  temperature_c: ["气温", "Temperature"], rain_probability: ["降水概率", "Chance of rain"], sunrise: ["日出", "Sunrise"],
  sunset: ["日落", "Sunset"], train: ["车次", "Train"], departure: ["发车", "Departs"], minutes: ["到站", "Arrives in"],
  status: ["状态", "Status"], delay_minutes: ["延误", "Delay"], available_spaces: ["空余车位", "Free spaces"],
  available_connectors: ["空闲充电枪", "Free connectors"], segment: ["封闭路段", "Closed section"],
  reopens_at: ["恢复通行", "Reopens"], opens: ["开门", "Opens"], closes: ["关门", "Closes"], quantity: ["库存", "In stock"],
  last_location: ["最新位置", "Last seen"], next_collection: ["下次收运", "Next pickup"], start: ["开始", "Starts"],
  end: ["结束", "Ends"], available: ["可用", "Available"], available_copies: ["可借副本", "Copies available"],
  exhibition: ["展览", "Exhibition"], hall: ["展厅", "Hall"], showtime: ["场次", "Showtime"], screen: ["影厅", "Screen"],
  performance: ["演出", "Performance"], starts: ["开始", "Starts"], opponent: ["对手", "Opponent"], open: ["开放", "Open"],
  reason: ["原因", "Reason"], public_lanes: ["公众泳道", "Public lanes"], title: ["日程", "Event"],
  remaining_days: ["剩余年假", "Days left"], window_start: ["最早送达", "Earliest"], window_end: ["最晚送达", "Latest"],
  expires: ["保修到期", "Expires"], coverage: ["保修范围", "Covers"], renews: ["续费日", "Renews"], amount: ["金额", "Amount"],
  currency: ["币种", "Currency"], due: ["截止日", "Due"], rate: ["汇率", "Rate"], postcode: ["邮编", "Postcode"],
  offset_minutes: ["UTC 偏移", "UTC offset"], is_holiday: ["公共假日", "Public holiday"], definition: ["释义", "Definition"],
  ipa: ["音标", "IPA"], allergens: ["过敏原", "Allergens"], usb_c_ports: ["USB‑C 接口", "USB‑C ports"],
  hdmi_ports: ["HDMI 接口", "HDMI ports"], aqi: ["AQI", "AQI"], category: ["类别", "Category"], level: ["等级", "Level"],
  uv_index: ["紫外线指数", "UV index"], level_m: ["水位", "Water level"], measured_at: ["测量时间", "Measured"],
  high_tide: ["高潮", "High tide"], decibels: ["噪声", "Noise"], active: ["生效预警", "Active alert"],
  ramp: ["坡道", "Ramp"], elevator: ["电梯", "Lift"], ssid: ["网络名", "Network"], method: ["连接方式", "Sign-in"],
  location: ["位置", "Location"], large_lockers: ["大件柜", "Large lockers"], operating: ["运营", "Operating"],
  next_departure: ["下一班", "Next departure"], empty_docks: ["空桩", "Empty docks"], main: ["主菜", "Main"],
  side: ["配菜", "Side"],
};

// How a yes/no result reads for each field: [true, false] per language.
const BOOLEANS = {
  active: [["有生效预警", "无生效预警"], ["Alert in effect", "No active alert"]],
  open: [["开放", "关闭"], ["Open", "Closed"]],
  operating: [["运营中", "停运"], ["Running", "Not running"]],
  is_holiday: [["公共假日", "非公共假日"], ["Public holiday", "Not a holiday"]],
  available: [["可用", "已占用"], ["Available", "Taken"]],
  ramp: [["有坡道", "无坡道"], ["Ramp", "No ramp"]],
  elevator: [["有电梯", "无电梯"], ["Lift", "No lift"]],
};

const ENUMS = {
  delayed: ["延误", "Delayed"], on_time: ["准点", "On time"], cancelled: ["已取消", "Cancelled"],
  in_transit: ["运输中", "In transit"], delivered: ["已送达", "Delivered"], approved: ["已批准", "Approved"],
  pending: ["审批中", "Pending"], rejected: ["已驳回", "Rejected"], closed: ["关闭", "Closed"], open: ["开放", "Open"],
  good: ["优", "Good"], moderate: ["良", "Moderate"], unhealthy: ["不健康", "Unhealthy"], low: ["低", "Low"],
  medium: ["中", "Medium"], high: ["高", "High"], captive_portal: ["网页认证", "Captive portal"],
  password: ["密码", "Password"], breakfast: ["早餐", "Breakfast"], lunch: ["午餐", "Lunch"], dinner: ["晚餐", "Dinner"],
  milk: ["牛奶", "Milk"],
};

const SOURCES = {
  local_personal_store: ["本地个人工作区 · 未同步外部账号", "Local organizer · no external account sync"],
  live_open_meteo: ["Open‑Meteo", "Open‑Meteo"],
  live_frankfurter_reference: ["Frankfurter 参考汇率", "Frankfurter reference rates"],
  local_iana_tzdb: ["IANA 时区库", "IANA tz database"],
  synthetic_sandbox_not_live_api: ["演示夹具", "Sandbox fixture"],
  live_tencent_quote: ["腾讯行情公开接口", "Tencent public quotes"],
  custom_endpoint: ["自定义工具 · 你的服务", "Custom tool · your endpoint"],
};

const REJECTIONS = {
  unknown_tool: ["工具名不存在于实际目录，请先发现工具", "Unknown catalog tool; discover an available capability first"],
  repeated_call_id: ["call_id 重复：同一个 ID 只能执行一次", "Repeated call_id: each ID can run once"],
  schema_not_visible: ["工具当前不在可调用 Schema 集合中", "The tool is not in the current callable schema set"],
  schema_version: ["Schema 版本不匹配", "Schema version mismatch"],
  authorization_required: ["个人类工具需要本次会话授权", "Personal tools need session permission"],
  action_fields: ["调用缺少必填外层字段，或包含不支持的字段", "Required call fields are missing or unsupported fields were supplied"],
  invalid_call_id: ["call_id 格式无效", "Invalid call_id"],
  invalid_action: ["调用不是合法的 JSON 对象", "The call isn’t a JSON object"],
  invalid_private_action: ["私有调用无法解析", "The private call couldn’t be parsed"],
  action_too_large: ["调用内容超过长度上限", "The call is too large"],
  tool_queue_full: ["进行中的调用过多", "Too many calls in flight"],
  call_budget: ["本次会话的调用次数已用完", "Session call budget used up"],
  unit_mismatch: ["调用不属于当前输入单元", "Call doesn’t belong to the current unit"],
  live_unavailable: ["实时数据源暂不可用", "Live data source unavailable"],
  fixture_disabled: ["未配置实时数据源", "No live data source configured"],
  not_live: ["这个工具没有实时数据源", "This tool has no live source"],
  closed: ["会话已结束", "Session closed"],
  missing_argument: ["缺少必填参数", "A required argument is missing"],
  unknown_argument: ["出现了 Schema 之外的参数", "An argument isn’t in the schema"],
  schema_type: ["参数类型与 Schema 不符", "An argument has the wrong type"],
  schema_enum: ["参数不在 Schema 允许的选项中", "An argument is outside the allowed choices"],
  schema_range: ["参数超出 Schema 允许的范围", "An argument is outside the allowed range"],
  schema_string: ["参数文本不符合 Schema", "An argument’s text doesn’t fit the schema"],
  invalid_json: ["调用不是完整的 JSON", "The call isn’t complete JSON"],
  duplicate_json_key: ["调用 JSON 有重复字段", "The call’s JSON repeats a key"],
  non_finite_json: ["调用 JSON 含非法数值", "The call’s JSON has a non-finite number"],
  invalid_query: ["检索语句为空或过长", "The search query is empty or too long"],
  private_budget: ["调用内容超过长度上限", "The call is too long"],
  multiple_private_actions: ["同一轮只能发起一次调用", "Only one call per turn"],
  unclosed_private_span: ["本轮结束时调用尚未闭合", "The turn ended before the call closed"],
  delegate_close_missing: ["调用 JSON 完整，但缺少结束标记；尚未执行", "Complete JSON is missing its closing marker; not executed"],
  private_json_incomplete: ["调用 JSON 未写完；尚未执行", "Incomplete call JSON; not executed"],
  private_json_invalid: ["调用 JSON 不合法；尚未执行", "Invalid call JSON; not executed"],
  nested_private_span: ["调用标记嵌套；本轮已隔离", "Nested call markers; quarantined"],
  unmatched_delegate_close: ["调用缺少起始标记；尚未执行", "Missing opening call marker; not executed"],
  private_action_too_large: ["调用超过安全长度；尚未执行", "Call exceeds safety length; not executed"],
  empty_update: ["修改调用没有提供实际变更字段", "Update contains no changed fields"],
  unknown_city: ["数据源找不到这个城市", "The data source doesn’t know this city"],
  unsupported_date: ["实时预报只覆盖前 5 天到后 15 天", "Live forecasts cover 5 days back to 15 ahead"],
  ambiguous_instant: ["时刻缺少时区", "The time has no zone"],
  invalid_currency: ["货币需为三位 ISO 代码", "Currencies need 3-letter ISO codes"],
  blocked_endpoint: ["数据源地址不在白名单内", "The endpoint isn’t allowlisted"],
  upstream_redirect: ["数据源意外重定向", "The data source redirected unexpectedly"],
  upstream_size: ["数据源返回内容过大", "The data source returned too much"],
  unknown_symbol: ["没有找到匹配的股票或指数", "No stock or index matches"],
  invalid_region: ["市场只能是 A 股、港股或美股", "Market must be cn, hk or us"],
  invalid_range: ["天数需在 7 到 365 之间", "Days must be 7–365"],
  upstream_empty: ["数据源这次没有返回数据", "The data source returned nothing"],
};
// The router reports malformed spans as plain sentences, before any tool code runs.
const ROUTER = [
  [/nested delegate/i, ["调用中出现了嵌套标记，本轮已隔离", "Nested call markers; the turn was quarantined"]],
  [/unmatched delegate closing/i, ["调用缺少起始标记", "A call closed without opening"]],
  [/before private JSON closed/i, ["本轮结束时调用 JSON 尚未闭合", "The turn ended before the call’s JSON closed"]],
  [/safety bound/i, ["调用内容超过安全上限", "The call exceeded the safety bound"]],
];
const LIVE_REASONS = [
  [/IANA time-zone mapping|No geocoding result/i, "unknown_city"],
  [/past five and next fifteen days/i, "unsupported_date"],
  [/three alphabetic ISO codes/i, "invalid_currency"],
  [/not allowlisted/i, "blocked_endpoint"],
  [/redirect/i, "upstream_redirect"],
  [/exceeds limit/i, "upstream_size"],
  [/No (tradable )?security matches|No quote for/i, "unknown_symbol"],
  [/Not enough reference rates|No index quotes/i, "upstream_empty"],
];

const DOMAIN_ICONS = { 出行: "route", 生活: "home", 文化: "ticket", 个人: "person", 信息: "book", 环境: "leaf", 设施: "building" };

export function toolTitle(name) {
  return TITLES[name] ? pick(TITLES[name]) : name || "";
}
export function domainIcon(domain) {
  return DOMAIN_ICONS[domain] || "bolt";
}
export function fieldLabel(field) {
  return FIELDS[field] ? pick(FIELDS[field]) : String(field).replace(/_/g, " ");
}
export function sourceLabel(source) {
  return SOURCES[source] ? pick(SOURCES[source]) : String(source || "").replace(/^live_/, "").replace(/_/g, " ");
}
export function rejectionText(code, message) {
  if (code === "live_unavailable") {
    // The executor wraps the adapter's own reason; name that reason when it is known.
    const inner = LIVE_REASONS.find(([pattern]) => pattern.test(message || ""));
    if (inner) return `${pick(REJECTIONS.live_unavailable)} · ${pick(REJECTIONS[inner[1]])}`;
  }
  return REJECTIONS[code] ? pick(REJECTIONS[code]) : message || code || "";
}
/** Router errors are sentences, or a JSON parser message when the span didn't parse. */
export function routerErrorText(error) {
  const text = typeof error === "string" ? error : error?.message || JSON.stringify(error || "");
  const known = ROUTER.find(([pattern]) => pattern.test(text));
  if (known) return pick(known[1]);
  return getLanguage() === "zh" ? "调用 JSON 无法解析" : "The call’s JSON didn’t parse";
}

const pad = (n) => String(n).padStart(2, "0");
function formatDate(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) return null;
  const [, y, m, d] = match.map(Number);
  const thisYear = new Date().getFullYear() === y;
  if (getLanguage() === "zh") return `${thisYear ? "" : `${y}年`}${m}月${d}日`;
  const month = new Date(Date.UTC(y, m - 1, d)).toLocaleString("en", { month: "short", timeZone: "UTC" });
  return `${month} ${d}${thisYear ? "" : `, ${y}`}`;
}
function formatDateTime(text) {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(text);
  if (!match) return null;
  return `${formatDate(match[1])} ${match[2]}:${match[3]}`;
}
/** One value in readable form, with the unit its field implies. */
export function formatValue(field, value) {
  const zh = getLanguage() === "zh";
  if (typeof value === "boolean") {
    const phrase = BOOLEANS[field];
    if (phrase) return phrase[zh ? 0 : 1][value ? 0 : 1];
    return zh ? (value ? "是" : "否") : value ? "Yes" : "No";
  }
  if (typeof value === "number") {
    switch (field) {
      case "temperature_c":
        return `${value}°C`;
      case "rain_probability":
        return `${Math.round(value * 100)}%`;
      case "minutes":
      case "delay_minutes":
        return zh ? `${value} 分钟` : `${value} min`;
      case "remaining_days":
        return zh ? `${value} 天` : `${value} days`;
      case "offset_minutes": {
        const sign = value < 0 ? "−" : "+";
        const abs = Math.abs(value);
        return `UTC${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
      }
      case "level_m":
        return `${value} m`;
      case "decibels":
        return `${value} dB`;
      case "rate":
        return String(Number(value.toPrecision(6)));
      default:
        return String(value);
    }
  }
  if (typeof value === "string") {
    if (ENUMS[value]) return pick(ENUMS[value]);
    return formatDateTime(value) || formatDate(value) || value;
  }
  if (Array.isArray(value)) return value.map((item) => formatValue(field, item)).join(zh ? "、" : ", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return value == null ? "—" : String(value);
}
/** Label/value rows for a set of arguments or result fields, in schema order. */
export function fieldRows(values) {
  return Object.entries(values || {}).map(([field, value]) => ({ field, label: fieldLabel(field), value: formatValue(field, value) }));
}
/** The single line a person would want first, e.g. "18°C · 降水 20%". */
export function headline(tool, result, args = {}) {
  if (!result || typeof result !== "object") return "";
  const zh = getLanguage() === "zh";
  const has = (...keys) => keys.every((key) => key in result);
  if (tool === "weather.day" && has("temperature_c", "rain_probability"))
    return `${formatValue("temperature_c", result.temperature_c)} · ${zh ? "降水" : "rain"} ${formatValue("rain_probability", result.rain_probability)}`;
  if (tool === "sun.daylight" && has("sunrise", "sunset"))
    return `${fieldLabel("sunrise")} ${result.sunrise} · ${fieldLabel("sunset")} ${result.sunset}`;
  if (tool === "stock.quote" && has("price", "change_pct"))
    return `${result.name || result.symbol || ""} ${result.price} ${result.change_pct > 0 ? "+" : ""}${result.change_pct}%`.trim();
  if (tool === "market.indices" && Array.isArray(result.indices) && result.indices.length) {
    const [first] = result.indices;
    return `${first.name} ${first.price} ${first.change_pct > 0 ? "+" : ""}${first.change_pct}%`;
  }
  if (tool === "currency.trend" && has("last", "change_pct"))
    return `${result.base}/${result.quote} ${result.last} ${result.change_pct > 0 ? "+" : ""}${result.change_pct}%`;
  if (tool === "currency.reference" && has("rate") && args.base && args.quote)
    return `1 ${args.base} = ${formatValue("rate", result.rate)} ${args.quote}`;
  if (has("amount", "currency")) return `${result.amount} ${result.currency}`;
  const [first] = Object.entries(result);
  if (!first) return "";
  const [field, value] = first;
  const text = formatValue(field, value);
  // Booleans already read as a phrase; other values get their label for context.
  return typeof value === "boolean" ? text : `${fieldLabel(field)} ${text}`;
}
/** Result fields worth listing under the headline (those it did not already state). */
export function detailRows(tool, result) {
  const rows = fieldRows(result);
  if (tool === "weather.day" || tool === "sun.daylight" || tool === "currency.reference") return [];
  if ("amount" in (result || {}) && "currency" in (result || {})) return rows.filter((row) => !["amount", "currency"].includes(row.field));
  return rows.slice(1);
}
const DIGITS = "零一二三四五六七八九";
/** Spoken Chinese for a whole number below 10000, as speech models write it (17 → 十七). */
function zhNumber(n) {
  if (!Number.isInteger(n) || n < 0 || n >= 10000) return null;
  if (n < 10) return DIGITS[n];
  if (n < 20) return "十" + (n % 10 ? DIGITS[n % 10] : "");
  let text = "";
  let gap = false;
  for (const [size, name] of [[1000, "千"], [100, "百"], [10, "十"], [1, ""]]) {
    const digit = Math.floor(n / size) % 10;
    if (digit) {
      text += (gap ? "零" : "") + DIGITS[digit] + name;
      gap = false;
    } else if (text) gap = true;
  }
  return text;
}
/** Strings in a spoken answer that echo the result: numbers (also as spoken Chinese),
 * percentages, clock times and names. Used only to highlight; a miss proves nothing. */
export function groundingTerms(result) {
  const terms = new Set();
  const number = (n) => {
    terms.add(String(n));
    const spoken = zhNumber(Math.abs(n));
    if (spoken) terms.add((n < 0 ? "零下" : "") + spoken + "度").add((n < 0 ? "负" : "") + spoken);
  };
  const visit = (field, value) => {
    if (typeof value === "number") {
      if (field === "rain_probability" || (value > 0 && value < 1)) {
        const percent = Math.round(value * 100);
        terms.add(`${percent}%`).add(`百分之${zhNumber(percent) || percent}`);
        if (percent === 100) terms.add("百分之百");
      } else number(value);
    } else if (typeof value === "string") {
      terms.add(value);
      const shown = formatValue(field, value);
      if (shown !== value) terms.add(shown);
      const time = /(?:^|T)(\d{2}):(\d{2})/.exec(value);
      if (time) {
        const [hours, minutes] = [Number(time[1]), Number(time[2])];
        terms.add(`${time[1]}:${time[2]}`).add(`${hours}:${time[2]}`);
        terms.add(`${zhNumber(hours)}点${minutes ? (minutes < 10 ? "零" : "") + zhNumber(minutes) + (minutes < 10 ? "分" : "") : ""}`);
      }
    } else if (Array.isArray(value)) value.forEach((item) => visit(field, item));
    else if (value && typeof value === "object") for (const [key, item] of Object.entries(value)) visit(key, item);
  };
  for (const [field, value] of Object.entries(result || {})) visit(field, value);
  // Single characters (a lone 六 or 0) would light up unrelated words.
  return [...terms].filter((term) => term && term.length >= 2).sort((a, b) => b.length - a.length);
}
