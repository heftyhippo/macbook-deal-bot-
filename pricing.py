"""
pricing.py - model detection from listing titles (Japanese and English),
currency conversion, landed-cost maths and deal scoring.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import requests

# ----------------------------------------------------------------------------
# Listing dataclass shared across the project
# ----------------------------------------------------------------------------

# which currency each source trades in, and which fee route applies
SOURCE_CURRENCY = {"mercari": "JPY", "yahoo": "JPY", "rakuma": "JPY",
                   "ebay_us": "USD", "swappa": "USD", "ebay_uk": "GBP"}
JP_SOURCES = {"mercari", "yahoo", "rakuma"}
US_SOURCES = {"ebay_us", "swappa"}
UK_SOURCES = {"ebay_uk"}

# alert thresholds are per REGION (JP prices genuinely run lower, so its bar
# is higher - see alerts.regions in config.yaml)
REGION_OF_SOURCE = {"mercari": "jp", "yahoo": "jp", "rakuma": "jp",
                    "ebay_us": "us", "swappa": "us", "ebay_uk": "uk"}

_THRESHOLD_DEFAULTS = {"uk": {"min": 35, "hot": 40, "too_good": 50},
                       "us": {"min": 35, "hot": 40, "too_good": 50},
                       "jp": {"min": 50, "hot": 55, "too_good": 65}}


def region_of(source: str) -> str:
    return REGION_OF_SOURCE.get(source, "uk")


def alert_thresholds(cfg: dict, source: str) -> dict:
    """{'min', 'hot', 'too_good'} savings-% thresholds for this source's
    region. Falls back to the old flat keys if alerts.regions is absent."""
    a = cfg.get("alerts", {})
    reg = region_of(source)
    base = dict(_THRESHOLD_DEFAULTS[reg])
    # legacy flat keys (pre-regional configs) apply to every region
    for old, new in (("min_savings_pct", "min"), ("hot_savings_pct", "hot"),
                     ("too_good_pct", "too_good")):
        if old in a:
            base[new] = float(a[old])
    for k, v in (a.get("regions", {}).get(reg) or {}).items():
        if k in base:
            base[k] = float(v)
    return base


def global_min_alert_pct(cfg: dict) -> float:
    """The lowest alert bar across all regions - below it nothing alerts."""
    return min(alert_thresholds(cfg, s)["min"] for s in REGION_OF_SOURCE)

CURRENCY_SYMBOL = {"JPY": "¥", "USD": "$", "GBP": "£"}

# grade -> condition bucket used by the fair-value maths
BUCKET_OF_GRADE = {"resale": "new", "personal": "like_new", "good": "good"}


@dataclass
class Listing:
    item_id: str               # e.g. "m12345678901" or "x1234567890"
    source: str                # "mercari" | "yahoo" | "rakuma" | "ebay_us"
    title: str
    price: float               # in the source's native currency (see currency)
    is_auction: bool = False   # True = auction current bid (price may rise)
    condition: str = ""        # text such as "新品、未使用" or "Open box"
    currency: str = "JPY"      # "JPY" | "USD"
    best_offer: bool = False   # eBay "or Best Offer" - real price may be lower
    grade: str = "resale"      # "resale" (new/unused, arbitrage-safe) |
                               # "personal" (practically new, buy-to-use) |
                               # "good" (light visible wear - value tier only)
    url: str = ""              # original marketplace URL
    buyee_path: str = ""       # exact Buyee item URL captured while scraping
                               # (used for sources where the ID format varies)
    # filled in during analysis:
    model_id: Optional[str] = None
    model_label: str = ""
    chip: str = ""
    size: Optional[int] = None
    size_guessed: bool = False
    ram_gb: Optional[int] = None
    storage_gb: Optional[int] = None
    keyboard: str = "unknown"  # "US" | "JIS" | "unknown"
    cycles: Optional[int] = None
    landed_gbp: float = 0.0
    uk_avg_gbp: float = 0.0
    savings_pct: float = 0.0
    # best-value analysis (section 3):
    uk_used_gbp: float = 0.0        # eBay-UK sold median for USED units
    fair_gbp: float = 0.0           # fair UK value for THIS condition
    value_landed_gbp: float = 0.0   # landed + pro-rated battery wear cost
    value_pct: float = 0.0          # % below fair value = the deal score
    spec_adj_gbp: float = 0.0       # benchmark shift for above/below-base spec
    flip_profit_gbp: float = 0.0    # est. profit reselling at UK avg (resale tier)
    flags: list = field(default_factory=list)

    @property
    def price_str(self) -> str:
        """Native price formatted for display, e.g. ¥218,000 / $1,499 / £999."""
        return f"{CURRENCY_SYMBOL.get(self.currency, '')}{self.price:,.0f}"

    @property
    def market_links(self) -> list:
        """(label, url) purchase links appropriate to the source's market."""
        if self.source in US_SOURCES or self.source in UK_SOURCES:
            label = {"ebay_us": "eBay US", "swappa": "Swappa",
                     "ebay_uk": "eBay UK"}.get(self.source, "Listing")
            return [(label, self.original_url)]
        return [("Buyee", self.buyee_url),
                ("ZenMarket", self.zenmarket_url),
                ("Original", self.original_url)]

    @property
    def buyee_url(self) -> str:
        if self.source in US_SOURCES:
            return ""
        if self.buyee_path:
            if self.buyee_path.startswith("http"):
                return self.buyee_path
            return "https://buyee.jp" + self.buyee_path
        if self.source == "mercari":
            return f"https://buyee.jp/mercari/item/{self.item_id}"
        if self.source == "rakuma":
            return f"https://buyee.jp/item/rakuma/{self.item_id}"
        return f"https://buyee.jp/item/yahoo/auction/{self.item_id}"

    @property
    def zenmarket_url(self) -> str:
        if self.source in US_SOURCES:
            return ""
        if self.source == "mercari":
            return f"https://zenmarket.jp/en/mercariproduct.aspx?itemCode={self.item_id}"
        if self.source == "rakuma":
            # ZenMarket also proxies Rakuma; if the code shape ever drifts this
            # still lands on a valid search for the item.
            return f"https://zenmarket.jp/en/rakuma.aspx?itemCode={self.item_id}"
        return f"https://zenmarket.jp/en/auction.aspx?itemCode={self.item_id}"

    @property
    def original_url(self) -> str:
        if self.url:
            return self.url
        if self.source == "ebay_us":
            return f"https://www.ebay.com/itm/{self.item_id}"
        if self.source == "ebay_uk":
            return f"https://www.ebay.co.uk/itm/{self.item_id}"
        if self.source == "swappa":
            return f"https://swappa.com/listing/view/{self.item_id}"
        if self.source == "mercari":
            return f"https://jp.mercari.com/item/{self.item_id}"
        if self.source == "rakuma":
            return f"https://item.fril.jp/{self.item_id}"
        return f"https://page.auctions.yahoo.co.jp/jp/auction/{self.item_id}"


# ----------------------------------------------------------------------------
# Title normalisation + model detection
# ----------------------------------------------------------------------------

RAM_SIZES = {8, 16, 18, 24, 32, 36, 48, 64, 96, 128}
STORAGE_GB_SIZES = {256, 512}

def normalise(text: str) -> str:
    """Full-width -> half-width, uppercase, katakana chip words -> latin."""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.upper()
    t = t.replace("プロ", " PRO ").replace("マックス", " MAX ")
    t = t.replace("インチ", "INCH").replace("型", "INCH")
    return t

def parse_listing_specs(listing: Listing) -> None:
    """Fill chip / size / ram / storage / keyboard on the listing in place."""
    t = normalise(listing.title)

    # Must look like a MacBook Pro, not an Air / Neo / accessory
    if "MACBOOK" not in t:
        return
    if "AIR" in t or "NEO" in t:
        return

    # --- chip ---------------------------------------------------------------
    m = re.search(r"\bM([2-5])\s*[-/]?\s*(PRO|MAX)?\b", t)
    if not m:
        return
    gen = int(m.group(1))
    variant = m.group(2) or ""
    # A plain "M2" MacBook Pro (13-inch) is OUT of scope; M2 PRO/MAX are in.
    if gen == 2 and not variant:
        return
    listing.chip = f"M{gen} {variant}".strip()

    # --- RAM / storage (strip them out before looking for screen size) -------
    storage_gb = None
    tb = re.search(r"\b([1248])\s*TB\b", t)
    if tb:
        storage_gb = int(tb.group(1)) * 1024
    gb_values = [int(x) for x in re.findall(r"\b(\d{2,4})\s*GB\b", t)]
    ram = None
    for v in gb_values:
        if v in RAM_SIZES and ram is None:
            ram = v
        elif v in STORAGE_GB_SIZES and storage_gb is None:
            storage_gb = v
    # lone "512GB" with no RAM figure: don't mistake storage for RAM
    listing.ram_gb = ram
    listing.storage_gb = storage_gb

    # --- screen size ----------------------------------------------------------
    t_nosizes = re.sub(r"\b\d{1,4}\s*(?:GB|TB)\b", " ", t)
    # core counts ("12C CPU", "16-core GPU", "16‑coreGPU", "16コア") are not
    # screen sizes; sellers use assorted unicode hyphens and skip spaces
    t_nosizes = re.sub(r"\b\d{1,2}\s*[-‐–—]?\s*(?:C\b|CORES?|コア)", " ", t_nosizes)
    s = re.search(r"(?<!\d)(14|16)(?:[.]2)?(?!\d)", t_nosizes)
    if s:
        listing.size = int(s.group(1))
    else:
        # an explicit 13"/15" with no 14/16 anywhere is a MacBook Air (or an
        # old 13" Pro) whose title just skips the word "Air" - out of scope
        if re.search(r"(?<!\d)(13|15)(?:[.]\d)?\s*(?:INCH|[\"”])", t_nosizes):
            listing.chip = ""
            return
        listing.size = 14          # conservative default: compare vs the cheaper size
        listing.size_guessed = True

    # --- keyboard layout -------------------------------------------------------
    # Only overwrite when the title actually states a layout: US-market
    # listings arrive with keyboard="US" already set (ANSI is the default
    # there) and that must survive a title that doesn't mention the keyboard.
    if re.search(r"US\s*配列|US\s*キー|英語\s*配列|英字\s*配列|US\s*KEYBOARD", t):
        listing.keyboard = "US"
    elif re.search(r"\bJIS\b|日本語\s*配列|JAPANESE\s*KEY", t):
        listing.keyboard = "JIS"
    elif re.search(r"\b(?:SWEDISH|GERMAN|FRENCH|ITALIAN|SPANISH|DANISH|"
                   r"NORWEGIAN|NORDIC|BELGIAN|PORTUGUESE|QWERTZ|AZERTY)\b", t):
        # in a Mac listing a bare nationality adjective means the keyboard
        listing.keyboard = "EU"    # non-UK ISO layout sold cross-border

    # sellers often put the cycle count right in the title - grab it for free
    if listing.cycles is None:
        listing.cycles = find_cycle_count(listing.title)


_BASE_SPEC_RE = re.compile(r"(\d+)\s*GB.*?/\s*(\d+)\s*(GB|TB)")


def _spec_adjust_gbp(listing: Listing, mdl: dict, cfg: Optional[dict]) -> float:
    """Benchmark shift for a listing specced above/below the model's
    base_spec - a 48GB/2TB machine should not be scored against base money.
    Additive GBP steps from config (value.spec_adjustments), capped so a
    parse mishap can't distort the benchmark by more than -15%/+60%."""
    if not cfg:
        return 0.0
    sa = cfg.get("value", {}).get("spec_adjustments", {})
    if not sa:
        return 0.0
    m = _BASE_SPEC_RE.search(str(mdl.get("base_spec", "")))
    if not m:
        return 0.0
    base_ram = int(m.group(1))
    base_ssd = int(m.group(2)) * (1024 if m.group(3) == "TB" else 1)
    adj = 0.0
    per8 = float(sa.get("ram_per_8gb_gbp", 60))
    if listing.ram_gb and listing.ram_gb > base_ram:
        adj += (listing.ram_gb - base_ram) / 8.0 * per8
    ssd_map = {int(k): float(v) for k, v in (sa.get("ssd_gbp") or {}).items()}
    if listing.storage_gb and ssd_map:
        adj += (ssd_map.get(listing.storage_gb, 0.0)
                - ssd_map.get(base_ssd, 0.0))
    new = float(mdl["uk_avg_gbp"])
    return round(min(max(adj, -0.15 * new), 0.60 * new), 2)


def match_model(listing: Listing, models: list[dict],
                cfg: Optional[dict] = None) -> None:
    """Attach the matching config model (chip + size) to the listing, with
    the benchmark adjusted for the listing's RAM/SSD spec when cfg given."""
    for mdl in models:
        if mdl["chip"].upper() == listing.chip and int(mdl["size"]) == listing.size:
            listing.model_id = mdl["id"]
            listing.model_label = f'{mdl["chip"]} {mdl["size"]}"'
            adj = _spec_adjust_gbp(listing, mdl, cfg)
            listing.spec_adj_gbp = adj
            new = float(mdl["uk_avg_gbp"])
            used = float(mdl.get("uk_used_gbp", 0) or 0)
            listing.uk_avg_gbp = round(new + adj, 2)
            # scale the used benchmark proportionally with the spec shift
            listing.uk_used_gbp = (round(used * listing.uk_avg_gbp / new, 2)
                                   if used and new else used)
            return


# ----------------------------------------------------------------------------
# Exclusion filter
# ----------------------------------------------------------------------------

def is_excluded(title: str, exclude_keywords: list[str]) -> Optional[str]:
    t = normalise(title)
    for kw in exclude_keywords:
        if normalise(kw) in t:
            return kw
    return None


# ----------------------------------------------------------------------------
# FX
# ----------------------------------------------------------------------------

def get_fx(fx_cfg: dict) -> tuple[dict, str]:
    """Live JPY-per-GBP and USD-per-GBP in one request (frankfurter, ECB data).
    Returns ({"JPY": x, "USD": y, "GBP": 1.0}, note)."""
    fallback = {"JPY": float(fx_cfg.get("fallback_jpy_per_gbp", 216)),
                "USD": float(fx_cfg.get("fallback_usd_per_gbp", 1.33)),
                "GBP": 1.0}
    try:
        r = requests.get(
            "https://api.frankfurter.dev/v1/latest",
            params={"base": "GBP", "symbols": "JPY,USD"},
            timeout=10,
        )
        r.raise_for_status()
        rates = r.json()["rates"]
        return {"JPY": float(rates["JPY"]), "USD": float(rates["USD"]),
                "GBP": 1.0}, "live"
    except Exception:
        return fallback, "fallback (edit fx.fallback_* in config.yaml)"


# ----------------------------------------------------------------------------
# Landed cost + scoring
# ----------------------------------------------------------------------------

def landed_cost_gbp(price: float, source: str, cfg: dict, rates: dict) -> float:
    """Full estimated cost delivered to a UK doorstep, in GBP.

    Japan route:  item + proxy fee + JP domestic + international shipping (JPY)
    US route:     item (+ sales tax) + forwarder fee + US domestic
                  + international shipping (USD)
    Both then:    x 1.20 UK import VAT (laptops carry 0% duty) + courier handling.
    """
    c = cfg["costs"]
    if source in UK_SOURCES:
        # domestic purchase: no import VAT, no courier clearance - just a
        # small postage buffer (many UK listings ship free)
        return round(price + float(c.get("uk_domestic_shipping_gbp", 8)), 2)
    if source in US_SOURCES:
        tax = 1 + float(c.get("us_sales_tax_pct", 0)) / 100.0
        total_usd = (price * tax
                     + c.get("us_forwarder_fee_usd", 12)
                     + c.get("us_domestic_shipping_usd", 10)
                     + c.get("us_intl_shipping_usd", 85))
        gbp = total_usd / rates["USD"]
    else:
        dom = c["domestic_shipping_jpy"].get(source, 0)
        total_jpy = price + c["proxy_fee_jpy"] + dom + c["intl_shipping_jpy"]
        gbp = total_jpy / rates["JPY"]
    if c.get("apply_uk_import", True):
        gbp = gbp * (1 + c["uk_vat_pct"] / 100.0) + c["courier_handling_gbp"]
    return round(gbp, 2)


def fair_value_gbp(listing: Listing, cfg: dict) -> float:
    """What a UK buyer typically pays for THIS model in THIS condition.

    new       -> the model's UK average for new/unused (uk_avg_gbp)
    good      -> the eBay-UK SOLD median for used units (uk_used_gbp),
                 refreshed by `ukprices`; falls back to a researched factor
    like_new  -> midpoint of the two (zero-wear units trade between the
                 used median and the new price)
    """
    v = cfg.get("value", {})
    new = float(listing.uk_avg_gbp)
    used = float(listing.uk_used_gbp or 0)
    if used and new:
        # eBay sold medians mix specs (a used median can even exceed the new
        # benchmark when high-RAM configs dominate) - keep the used figure
        # inside a sane band relative to new
        used = min(max(used, new * 0.55), new * 0.92)
    bucket = BUCKET_OF_GRADE.get(listing.grade, "good")
    if bucket == "new":
        return new
    if bucket == "like_new":
        if used:
            return round((new + used) / 2, 2)
        return round(new * float(v.get("like_new_factor", 0.88)), 2)
    if used:
        return used
    return round(new * float(v.get("good_factor", 0.78)), 2)


def battery_wear_gbp(cycles: Optional[int], cfg: dict) -> float:
    """Battery life already consumed, priced pro-rata: each cycle uses
    1/1000th of the battery's rated life, and a replacement costs a known
    amount - so 600 cycles on a £249 battery = £149 of value gone. Smooth,
    no cliffs; negligible (<£15) below 60 cycles."""
    if not cycles:
        return 0.0
    v = cfg.get("value", {})
    rating = float(v.get("battery_cycle_rating", 1000))
    cost = float(v.get("battery_replacement_gbp", 249))
    return round(min(cycles, rating) / rating * cost, 2)


def value_score(listing: Listing, cfg: dict) -> None:
    """Deal quality for the price RELATIVE TO CONDITION: % below the fair
    UK value for this model in this condition, after adding the pro-rated
    battery wear cost to the landed price."""
    listing.fair_gbp = fair_value_gbp(listing, cfg)
    wear = battery_wear_gbp(listing.cycles, cfg)
    listing.value_landed_gbp = round(listing.landed_gbp + wear, 2)
    if wear >= 25:
        flag = f"battery wear costed in (+£{wear:.0f})"
        if flag not in listing.flags:
            listing.flags.append(flag)
    if listing.fair_gbp:
        listing.value_pct = round(
            (listing.fair_gbp - listing.value_landed_gbp)
            / listing.fair_gbp * 100, 1)


def max_cycles_for(grade: str, cfg: dict) -> int:
    """Battery-cycle ceiling per tier: strict for resale-grade "unused"
    machines, 60 for practically-new personal buys (~6% of Apple's
    1000-cycle rating - battery health still within new-unit variance),
    and the value tier's hard floor for "good" listings (beyond it the
    machine counts as heavily used and is dropped anyway)."""
    if grade == "personal":
        return int(cfg.get("personal", {}).get("max_battery_cycles", 60))
    if grade == "good":
        return int(cfg.get("value", {}).get("max_battery_cycles", 800))
    return int(cfg["alerts"]["max_battery_cycles"])


def flip_profit_gbp(listing: Listing, cfg: dict) -> float:
    """Estimated profit reselling a resale-grade unit at the UK average:
    sale proceeds after friction (postage/packaging/pricing-to-sell) minus
    the landed cost. Only meaningful for new/unused stock."""
    friction = float(cfg.get("resale", {}).get("sell_friction_pct", 5))
    if not listing.uk_avg_gbp or not listing.landed_gbp:
        return 0.0
    return round(listing.uk_avg_gbp * (1 - friction / 100.0)
                 - listing.landed_gbp, 2)


def score(listing: Listing, cfg: dict, rates: dict) -> None:
    listing.landed_gbp = landed_cost_gbp(listing.price, listing.source, cfg, rates)
    if listing.uk_avg_gbp:
        listing.savings_pct = round(
            (listing.uk_avg_gbp - listing.landed_gbp) / listing.uk_avg_gbp * 100, 1
        )
    if listing.grade == "resale":
        listing.flip_profit_gbp = flip_profit_gbp(listing, cfg)
    a = cfg["alerts"]
    # Price-sanity backstop: a whole MacBook never sells for a small fraction
    # of its UK value - that's a part (screen/board/etc.) or an accessory the
    # keyword list didn't catch. Flag (and below, suppress alerts) when the
    # asking price is implausibly low for the model. Default 0.30 = 30% of UK
    # avg; genuine bargains (even -50% landed) sit far above this floor.
    floor_ratio = a.get("implausible_price_ratio", 0.30)
    rate = rates.get(listing.currency)
    if listing.uk_avg_gbp and rate:
        bare_gbp = listing.price / rate   # bare item price, no fees
        if bare_gbp < listing.uk_avg_gbp * floor_ratio:
            listing.flags.append(
                "PRICE TOO LOW for a whole unit - likely a part/accessory, not the Mac")
    if listing.spec_adj_gbp:
        listing.flags.append(
            f"benchmark spec-adjusted {listing.spec_adj_gbp:+,.0f} GBP")
    if listing.savings_pct >= alert_thresholds(cfg, listing.source)["too_good"]:
        listing.flags.append("TOO-GOOD? verify carefully (box-only/scam/mislabel risk)")
    if listing.is_auction:
        listing.flags.append("auction - current bid, price can rise")
    if listing.best_offer:
        listing.flags.append("accepts Best Offer - real price may be lower")
    if listing.size_guessed:
        listing.flags.append('size not stated - assumed 14"')
    if listing.keyboard == "JIS":
        listing.flags.append("JIS (Japanese) keyboard")
    elif listing.keyboard == "EU":
        listing.flags.append("non-UK European keyboard layout")
    elif listing.keyboard == "unknown" and listing.source in JP_SOURCES:
        listing.flags.append("keyboard layout unknown (likely JIS)")
    max_cyc = max_cycles_for(listing.grade, cfg)
    if listing.cycles is not None and listing.cycles > max_cyc:
        listing.flags.append(f"battery cycles {listing.cycles} > your max {max_cyc}")


CYCLE_RE = re.compile(
    r"(?:充放電回数|充放電|サイクル数|サイクルカウント|サイクル|CYCLE\s*COUNTS?|CYCLES?)\D{0,8}?(\d{1,4})\s*回?",
    re.IGNORECASE,
)
# English word order with the number first: "only 3 cycles", "12 battery cycles"
CYCLE_EN_RE = re.compile(r"\b(\d{1,4})\s*(?:BATTERY\s+)?(?:CHARGE\s+)?CYCLES?\b")

def find_cycle_count(text: str) -> Optional[int]:
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text).upper()
    for rx in (CYCLE_RE, CYCLE_EN_RE):
        m = rx.search(t)
        if m:
            try:
                n = int(m.group(1))
                if 0 <= n <= 3000:
                    return n
            except ValueError:
                pass
    return None
