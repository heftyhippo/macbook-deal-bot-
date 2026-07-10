# Apple Deal Bot 🍏 — Japan · US · UK · EU

Scans **ten markets** — Mercari Japan, Yahoo! Auctions, Rakuma and PayPay
Flea Market (the marketplaces behind Buyee/ZenMarket), eBay US, Swappa,
Craigslist, eBay UK, Gumtree and eBay Germany — for **near-new Apple
hardware to resell at a profit**:

- **MacBook Pro** 14"/16" (M2 Pro generation onwards)
- **Mac mini** (M2/M4 gens), **Mac Studio** (all gens), **iMac 24"**
  (M3/M4), **Mac Pro** (M2 Ultra)
- **Apple Studio Display**
- **iPad Pro** (M2/M4/M5) and **iPad Air** (the 2022+ models that average
  over ~£500)

Only **near-new stock** qualifies: new/unused (resale-grade) or like-new
with zero visible wear. For every listing it estimates the **full landed
cost in GBP** (item + proxy/forwarder fees + shipping scaled to the product
+ UK import VAT where applicable), compares it against the UK going rate,
and surfaces the best finds — as **WhatsApp alerts** and on a **dashboard
website** with two views: **Best flips** (ranked by estimated resale profit
with ROI) and **Biggest savings** (ranked by the raw %-below-UK-average the
alerts fire on).

The dashboard is written to `deals.html` after every local scan — and if you
follow **`CLOUD_SETUP.md`** (recommended, ~20 min once), GitHub's servers run
the scan every 20 minutes for free and host the dashboard as a real website
you can open from **any device, any time**, with your computer off.

> **Why not Facebook Marketplace / OfferUp / Mercari US / Vinted?** All were
> evaluated (July 2026). Facebook Marketplace sits behind a login wall —
> scraping it requires an account session, breaches its terms and gets
> accounts banned. OfferUp blocks all automated access outright and Mercari
> US captchas every request. Vinted's electronics section needs a rotating
> app session and stocks very few Macs. Craigslist and Gumtree made the cut
> instead as the classifieds sources — with the caveat that classifieds have
> **no buyer protection** and are mostly local-pickup: treat those finds as
> leads to follow up, not one-click buys.

> **How Yahoo Auctions is reached:** Yahoo! JAPAN has geo-blocked all visitors
> from the UK and EEA since April 2022, so the bot searches **Buyee's mirror**
> of Yahoo Auctions instead (Buyee exists precisely to give overseas buyers
> access). This also means the "Original" link for Yahoo items in `deals.html`
> won't open from a UK connection - use the Buyee or ZenMarket links for those.
> Mercari items' original links work fine.

> **eBay UK is scanned too:** domestic listings have no import VAT, no
> international shipping risk, UK (ISO) keyboards and UK returns — an
> underpriced UK listing beats an import every time, and they dominate the
> Best value tab for exactly that reason.

> **Which US markets and why:** every big US resale marketplace was evaluated
> (July 2026). **eBay US** is the main hunting ground — by far the largest
> inventory of Brand New / Open Box MacBook Pros, "Best Offer" haggling, buyer
> protection, and the only one a UK buyer can purchase from **directly** (many
> sellers ship worldwide via eBay International Shipping) instead of renting a
> US parcel-forwarder address. **Swappa** (human-verified listings, New + Mint
> only) is scanned too, but its Cloudflare bot-wall demands a real,
> stealth-patched Chrome — see the note below. **OfferUp** blocks everything
> outright, **Mercari US** captchas every request (and its parent company has
> been publicly wavering on keeping the US marketplace at all), and
> **Facebook Marketplace** requires a login — those three are out.

> **How Swappa is reached:** Swappa refuses plain HTTP clients *and* vanilla
> headless browsers. The bot drives your real installed **Google Chrome**
> (via the `patchright` library) with its automation fingerprints hidden.
> The window is sent **straight to the Dock, minimized** — it never appears
> on screen — and Chrome is **closed the moment the scan finishes**, so
> nothing lingers between scans. (Only if Cloudflare ever throws a fresh
> challenge that won't clear minimized does a window briefly appear to solve
> it — rare, because the solved cookie is kept in `.swappa_chrome_profile/`.)
> Requires Google Chrome installed; works from home connections, not cloud
> runners. Don't want any of this? Delete `swappa` from `sources:` in
> `config.yaml`.

For every JP deal it outputs direct purchase links via **Buyee** and
**ZenMarket**, plus the original Japanese listing; eBay US deals link straight
to the listing.

---

## 1. One-time setup (about 15 minutes)

### Step A — Install Python

1. Go to https://www.python.org/downloads/ and download Python 3.12 (or newer).
2. Run the installer.
   - **Windows: tick the box that says "Add python.exe to PATH"** before clicking
     Install. This is the single most important checkbox.
   - Mac: just run the installer normally (or `brew install python` if you use Homebrew).
3. To check it worked, open a terminal:
   - **Windows:** press the Windows key, type `cmd`, press Enter.
   - **Mac:** open the **Terminal** app.

   Then type:
   ```
   python --version
   ```
   (on Mac you may need `python3 --version`). You should see something like
   `Python 3.12.x`. If Windows says "python is not recognized", re-run the installer
   and tick the PATH box.

### Step B — Install the bot's dependencies

1. Unzip `macbook-deal-bot.zip` somewhere easy, e.g. your Documents folder.
2. In your terminal, move into that folder:
   ```
   cd Documents\macbook-deal-bot        (Windows)
   cd ~/Documents/macbook-deal-bot      (Mac)
   ```
3. Install the libraries the bot needs (one-off):
   ```
   python -m pip install -r requirements.txt
   python -m playwright install chromium
   ```
   (use `python3` instead of `python` on Mac if needed — that applies to every
   command in this guide.) The second command downloads a small invisible
   Chromium browser (~150 MB, one-off) — Buyee's anti-bot check requires real
   browser JavaScript, and this is how the bot passes it.

That's it — the bot now works. Try it:
```
python macdeals.py selftest
python macdeals.py scan --demo
```
The demo uses fake listings so you can see what the output looks like without
touching the internet.

### Step C — WhatsApp alerts (optional but recommended)

This is what lets the bot message your phone when it spots a great deal.
It uses **CallMeBot**, a free WhatsApp-API service for personal use — no
account, no card, one-time 2-minute setup:

1. Add this number to your phone's contacts: **+34 644 53 78 49**
   (name it "CallMeBot" or anything you like; if it ever stops working,
   check https://www.callmebot.com for the current number).
2. Send it this exact WhatsApp message:
   `I allow callmebot to send me messages`
3. Within a couple of minutes it replies with your personal **apikey**
   (a number like `123456`). Copy it.
4. Open `config.yaml` (in the bot folder) with TextEdit and fill in:
   ```yaml
   whatsapp:
     enabled: true
     phone: "+447712345678"   # YOUR WhatsApp number, with country code
     apikey: "123456"
   ```
5. Test it:
   ```
   python macdeals.py test-whatsapp
   ```
   You should get a WhatsApp message within seconds.

(Why WhatsApp and not iMessage/SMS? iMessage can only be sent by a Mac
that's awake — useless once the bot runs in the cloud — and SMS gateways
all cost money. CallMeBot's WhatsApp API is free and works from anywhere.)

---

## 2. Everyday use

| What you want | Command |
|---|---|
| Scan right now, show best deals | `python macdeals.py scan` |
| Scan without sending WhatsApp alerts | `python macdeals.py scan --no-alert` |
| Scan only some markets this run | `python macdeals.py scan --sources mercari,ebay_us` |
| Also save results to a spreadsheet | `python macdeals.py scan --csv deals.csv` |
| Scan on a loop in this terminal, alert me | `python macdeals.py watch` |
| ...with a custom full-scan interval (minutes) | `python macdeals.py watch --interval 30` |
| **Turn background scanning ON** (no terminal needed) | `python macdeals.py background start` |
| **Turn background scanning OFF** | `python macdeals.py background stop` |
| Is background scanning running? | `python macdeals.py background status` |
| Refresh UK average prices from eBay UK sold listings | `python macdeals.py ukprices` |
| ...and write those averages into config.yaml | `python macdeals.py ukprices --write` |
| Send a WhatsApp test message | `python macdeals.py test-whatsapp` |
| Check the bot's logic is healthy | `python macdeals.py selftest` |

Every scan also writes **`deals.html`** in the bot folder — double-click it to
open the dashboard in your browser: the **Buy for myself** and **Resell for
profit** views, with search, filters, sortable columns and clickable
Buyee / ZenMarket / eBay / Swappa links for every deal found. (The cloud setup
publishes this same dashboard as a website — see `CLOUD_SETUP.md`.)

### Background mode in practice — built to WIN deals, not just see them

Great deals last **minutes**. Watch mode is therefore built around speed:

- **Two cadences.** A full scan of every source runs every
  `watch_interval_minutes` (default 20), and in between the bot re-checks
  just the cheap, fast markets (`fast_sources`: Mercari + both eBays) every
  `fast_interval_minutes` (default 5). A fresh Mercari or eBay bargain is
  spotted within ~5 minutes of being listed.
- **Alerts fire per source, immediately.** The moment one market's listings
  are processed, qualifying deals go to your WhatsApp — a Mercari find never
  waits for Swappa's browser to finish the scan.
- **Background scanning with an on/off switch.** No terminal window needed,
  and it only ever runs when you say so:
  ```
  python3 macdeals.py background start    # ON  - scans until you stop it
  python3 macdeals.py background stop     # OFF
  python3 macdeals.py background status   # which is it right now?
  ```
  While ON it restarts itself if it crashes; it does **not** start at login
  and does **not** survive a reboot — after a restart it stays off until you
  `background start` again. Check on it any time with `tail -f macdeals.log`.
  If the Mac goes to sleep, scanning simply pauses and picks up again on
  wake — no need to touch your sleep settings.

**The 60-second playbook when your phone buzzes:** open the alert → tap the
link (eBay/Buyee/ZenMarket/Swappa) → sanity-check photos + the flags in the
message → buy or offer. Being SET UP beforehand is most of winning: keep
WhatsApp notifications ON and loud, install the eBay app, keep Buyee and
ZenMarket accounts logged in with a card saved (JP checkouts are 5+ taps if
you're not), and know your walk-away numbers per model in advance. For eBay
"Best Offer" listings, a fast reasonable offer usually beats a slow full-price
click from someone else.

**Don't want your Mac involved at all?** See **`CLOUD_SETUP.md`** — GitHub's
servers run the scan every 20 minutes for free, send the same WhatsApp
alerts, and host the dashboard as a website you can check from any device.
Note the cloud and local copies share no memory: if both are running with
alerts on you may get the same deal twice (fine as redundancy). Local scans
are still the only way to cover Swappa.

---

## 3. What the dashboard shows

`deals.html` has **two views**, both resale-first:

- **💰 Best flips** — every near-new find (new/unused *or* like-new), ranked
  by **estimated resale profit**: sell at the UK going rate for its
  condition (new stock at the UK average, like-new stock at like-new money),
  minus a configurable ~5% selling friction (`resale:` in `config.yaml`),
  minus the landed cost. ROI% shown alongside.
- **💸 Biggest savings** — the same stock ranked by the **raw saving**:
  landed cost vs the UK average for a new unit. The exact number the
  WhatsApp alerts fire on (★ rows clear your alert bar).

Both views have search, product-family / model / market filters, "hide JIS
keyboards", "hide auctions", an "alert-worthy only" switch, sortable
columns, and NEW badges on listings that appeared since you last looked.
Classifieds finds (Craigslist/Gumtree) carry a red no-buyer-protection chip.

Under the hood, every listing is graded into a condition tier — these drive
the scoring and the alerts:

1. **🏪 RESALE-GRADE — new / unopened / unused.** What the bot always hunted:
   sealed or never-used machines that can be resold as new-ish, so the deal
   works as arbitrage. Battery-cycle ceiling: **10**.
2. **🎯 PRACTICALLY NEW — buy-to-use.** Lightly used machines whose usage
   makes no practical difference to condition or longevity — noticeably
   cheaper to buy, but not resale stock. The bar is deliberately strict on
   both axes:
   - **Zero visible wear** — only the top used grade of each market qualifies:
     Mercari 目立った傷や汚れなし ("no visible scratches or dirt"), JP titles
     saying 美品/極美品/新品同様, Swappa **Mint** (human-verified), and eBay
     **Used** only when the title itself claims like-new/mint/pristine/low
     cycles. One grade lower ("Good", やや傷あり) admits visible wear and is
     excluded.
   - **≤ 60 battery cycles** (when stated; unknown is allowed but flagged).
     Apple rates these batteries at 1,000 cycles to 80% capacity, so 60
     cycles ≈ 6% of rated life — battery health still ~98%+, which is inside
     the unit-to-unit variance of brand-new machines. It's about 1–2 months
     of light use: too little to wear a keyboard, trackpad or hinge.
   Savings are still measured against the **same UK average for a new unit** —
   a practically-new alert literally reads "this much cheaper than buying new".
   Tune both knobs under `personal:` in `config.yaml` (set `enabled: false`
   to go back to new-only scanning).
3. **💎 BEST VALUE — the top 100 deals for price relative to condition.**
   Every buyable listing from every market and every condition tier (down to
   "good": light visible wear — Mercari やや傷や汚れあり, Swappa Good, plain
   eBay Used), re-scored by an algorithm that asks *"how far below the fair
   UK price for this model IN this condition is the all-in cost?"*:
   - **Fair value per condition** comes from real market data: new = the
     model's UK average; good = the **eBay UK sold median for used units**
     (refresh with `ukprices --write`); like-new = the midpoint. Researched
     fallback factors (88% / 78% of new) cover models without enough sold
     data, and used medians are sanity-clamped to 55–92% of new so spec-mix
     noise can't invert the ladder.
   - **Battery wear is priced in, smoothly**: each stated cycle consumes
     1/1000th of a £249 UK battery service, added to the landed cost before
     scoring — 40 cycles ≈ £10 (noise), 600 cycles ≈ £149 (a real haircut).
   - **Baselines**: worse-than-"good" condition never enters the bot; over
     **800 cycles** is excluded outright (that much use *is* a poor-condition
     signal); auctions are excluded (a current bid isn't a price you can
     pay); "brand new" listings stating a well-used battery are treated as
     mislistings; and anything priced below **45% of its condition-fair
     value** is dropped as scam/damage territory.
   This ranking is analysis-only: **no WhatsApp alerts** fire from it. All
   knobs live under `value:` in `config.yaml`.

## 4. How the bot decides what's a "deal"

For every listing it computes an **estimated landed cost**:

**Japan listings (Mercari / Yahoo / Rakuma):**
```
item price (¥)
+ proxy service fee            (default ¥800 — ZenMarket's Mercari fee; Buyee is often cheaper)
+ Japan domestic shipping      (¥0 Mercari — usually seller-paid; ¥1,200 Yahoo estimate)
+ international shipping       (¥8,000 estimate for a laptop by air)
→ converted to GBP at the live exchange rate
× 1.20  (UK import VAT — charged on goods + shipping)
+ £12   (courier handling fee, e.g. DHL/FedEx disbursement)
```

**US listings (eBay US):**
```
item price ($)
+ US sales tax                 (default 0% — exports and no-sales-tax forwarder
                                states aren't taxed; see config comments)
+ US domestic shipping         ($10 buffer — many listings ship free)
+ package-forwarder fee        ($12 — skipped entirely if the seller ships to
                                the UK directly / via eBay International Shipping,
                                so real cost is often a bit better)
+ international shipping       ($85 estimate, forwarder → UK express, 2-4 kg)
→ converted to GBP at the live exchange rate
× 1.20  (UK import VAT — laptops are 0% customs duty)
+ £12   (courier handling fee)
```

That landed GBP figure is compared with the **UK average price** for the exact
model (stored in `config.yaml`, see section 5). The difference is the **saving %**.

**Alert thresholds** (changeable in `config.yaml` under `alerts:`) are
**per region** and **split by whether the product ships with a keyboard**.
JP prices genuinely run lower, so its bar is higher — but the *size* of that
premium depends on the product: a Japanese MacBook or iMac carries a JIS
keyboard (a real UK-resale handicap), while a Mac mini, Mac Studio, Studio
Display or iPad from Japan is the identical product you'd buy here, so its
foreign premium only covers forwarding hassle:

| Region | MacBook / iMac 📣 | Keyboardless 📣 | 🔥 hot | ⚠️ *suspicious* |
|---|---|---|---|---|
| 🇬🇧 eBay UK / Gumtree | **≥ 35%** | **≥ 35%** | +5 | +15 |
| 🇺🇸 eBay US / Swappa / Craigslist | **≥ 35%** | **≥ 35%** | +5 | +15 |
| 🇩🇪 eBay Germany | **≥ 38%** | **≥ 35%** | +5 | +15 |
| 🇯🇵 Mercari / Yahoo / Rakuma / PayPay | **≥ 50%** | **≥ 42%** | +5 | +15 |

All percentages are savings on the **full landed cost** (fees, shipping
scaled to the product, and VAT included) vs the UK average. Deals below the
alert bar still appear on the dashboard — they just don't buzz your phone.
The ⚠️ level still alerts, but flagged: likely a scam, box-only, or
mis-listed item — read the listing very carefully.

Other guardrails baked in:

- Only Mercari condition grades **新品、未使用** (brand new, unused) and
  **未使用に近い** (almost unused) are fetched; Yahoo is searched with its
  "unused" filter; eBay US is searched with the **Brand New + Open Box**
  condition filters (and Buy-It-Now only, so the price shown is payable now —
  drop `LH_BIN=1` from `ebay_us_extra_params` in config to include auctions);
  Swappa is kept to its **New + Mint** grades (listings there are
  human-reviewed before going live, and each card states the exact
  chip/RAM/storage, so matching is precise).
- Listings mentioning ジャンク (junk), 箱のみ (box only), 整備済 (refurbished),
  parts-only, broken, etc. are excluded automatically — plus English trap words
  for eBay (refurb, for parts, cracked, activation lock / MDM, box only,
  local-pickup-only listings a UK buyer can't receive, ...).
- eBay listings that accept a **Best Offer** are flagged — the displayed
  saving is the floor, not the ceiling; haggle.
- If a seller states a **battery cycle count** (in the title or description, e.g.
  「充放電回数：4回」), the bot reads it. Anything over **10 cycles** is never
  alerted (edit `max_battery_cycles` to change). "cyc?" in the results means the
  seller didn't state it — worth asking via the proxy's "contact seller" feature.
- **Keyboard layout** is flagged: JP listings are usually JIS layout. "US配列" /
  "USキーボード" in the listing = US layout (closest to UK). The bot can't detect
  genuine UK layout — it's near-nonexistent on the JP market.
- The same item won't alert you twice unless its price drops a further 5%+.

---

## 5. UK price benchmarks — keep them fresh

`config.yaml` holds two benchmark prices per model:

- `uk_avg_gbp` — what a UK buyer pays for a **new / open-box-unused** unit
  (drives the resale + practically-new savings figures);
- `uk_used_gbp` — the **eBay UK sold median for used units** (drives the
  condition-aware fair values in the Best value tab).

Both are refreshed from real recent eBay UK SOLD listings (medians only
written when there are **10+ sales** behind them — small samples are noise):

```
python macdeals.py ukprices          # shows fresh eBay-UK-sold medians, changes nothing
python macdeals.py ukprices --write  # also updates config.yaml
```

You can always hand-edit any `uk_avg_gbp:` number in `config.yaml` if you know
better — it's your benchmark.

---

## 6. Things to know before you buy (important!)

1. **Keyboards: JP listings are JIS, US listings are ANSI.** Japanese MacBooks
   have the JIS layout (extra keys, different Enter) — fine for many people,
   dealbreaker for others; JP listings flagged `US` are US-layout exports.
   eBay US machines are ANSI ("US") layout, the closest thing to UK you'll
   find — true UK (ISO-GB) layout effectively doesn't exist on either market.
2. **"未使用" / "Open Box" relies on the seller's honesty.** The cycle-count
   check helps, but when in doubt ask the seller (Buyee/ZenMarket's question
   feature, or eBay messages) for a screenshot of System Report → Power showing
   the cycle count, before bidding/buying.
3. **Auctions vs Buy-It-Now.** Yahoo listings marked as auctions show the *current*
   bid — the final price can be much higher. The bot prefers the Buy-It-Now (即決)
   price when one exists. eBay US is searched Buy-It-Now-only by default, and
   listings that accept a Best Offer are flagged — try a cheeky offer.
4. **Fees are estimates.** Proxy/forwarder fees, shipping, and the courier
   handling charge vary by service, plan, weight and courier. The bot's defaults
   are deliberately slightly pessimistic, so real landed cost is usually a touch
   *better* than shown. Tune every number under `costs:` in `config.yaml`.
   For eBay US items sold with **eBay International Shipping**, the checkout
   quotes you the exact all-in figure (shipping + UK VAT) before you commit —
   compare it against the bot's estimate.
5. **VAT is not optional.** UK import VAT (20%) applies above £135 — every MacBook,
   from Japan or the US alike. Some couriers collect it on delivery rather than at
   checkout. The bot already includes it; don't be surprised when DHL emails you
   for payment. (Laptops carry 0% customs duty from both countries.)
6. **US sales tax.** Buying an export (seller ships to the UK) isn't US-taxed.
   If you use a US parcel forwarder, pick one in a no-sales-tax state
   (Oregon, Delaware, Montana, New Hampshire) and keep `us_sales_tax_pct: 0`;
   a forwarder in e.g. Florida means ~7% — set it in `config.yaml`.
7. **Watch for corporate stock in US listings.** Ex-company machines can be
   MDM/DEP-enrolled ("remote management") — a nasty surprise on first boot. The
   bot excludes titles admitting it (MDM / activation lock / demo unit), but ask
   the seller if a "new open box" price looks too easy.
8. **Apple warranty:** Apple's limited warranty for Mac is generally honoured
   internationally, but AppleCare bought in Japan/the US and consumer-law rights
   differ. Check serial coverage at checkcoverage.apple.com after purchase.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `python` not recognised (Windows) | Re-run the Python installer, tick "Add to PATH". Or use `py` instead of `python`. |
| `ModuleNotFoundError: mercapi` (etc.) | Run `python -m pip install -r requirements.txt` again, in the bot folder. |
| WhatsApp test fails | Phone or apikey has a typo (keep the quotes, include the country code), or you never sent the activation message to CallMeBot (README step C). If it worked before and stopped, check https://www.callmebot.com — the activation number occasionally changes. |
| Yahoo/Buyee error mentioning Playwright | Run the two one-off commands: `python -m pip install playwright` then `python -m playwright install chromium`. |
| Scan finds 0 Yahoo/Buyee items | Run `python macdeals.py scan --debug` — it saves raw pages as `debug_buyee_*.html` (or `debug_buyee_blocked.html` if Buyee refused the request). Send those files to Claude; Buyee occasionally changes its page layout. |
| Scan finds 0 eBay US items | Occasional one-offs are normal (eBay serves several page layouts; the bot handles the known ones and retries once). If it *persists*, run `python macdeals.py scan --debug` and send `debug_ebay_us_*.html` to Claude. |
| Swappa errors about patchright / Chrome | Swappa needs two things the other sources don't: `python3 -m pip install patchright`, and Google Chrome installed. No Chrome / no desktop session (e.g. a headless server) = Swappa gets skipped; everything else still works. |
| Swappa "challenge did not clear" | Cloudflare escalated for your connection. Delete the `.swappa_chrome_profile` folder and rescan; if it persists, Swappa may have tightened things — run with `--debug` and send `debug_swappa_blocked.html` to Claude. |
| Gumtree returns 0 / "HTTP 247" | Gumtree rate-limits query bursts aggressively. The bot already paces itself; if you scanned repeatedly in a short window, wait 15–30 min and it recovers on its own. |
| Craigslist finds look unbuyable | That's the nature of the source: local pickup + cash, no shipping, no buyer protection. Treat them as leads (a US friend, or message the seller about posting) — the red chip on the dashboard reminds you. |
| Yahoo/Buyee scan is slow on first query | Normal: the invisible browser solves Buyee's bot-check once (a few seconds), then the bot reuses the earned token at full speed. |
| Lots of weird matches / misses | Check `queries:` in config — you can add/remove search phrases freely. |
| Exchange rate shows "(fallback)" | The free FX API was unreachable; the bot used `fx.fallback_jpy_per_gbp` from config. Update that number occasionally. |
| It alerted a scammy-looking listing | That's what the ⚠️ ≥55% flag is for — the bot surfaces, you judge. Add recurring junk words to `filters.exclude_keywords`. |

**A honest note on scrapers:** Mercari, Yahoo and eBay change their websites from
time to time. When that happens a source may suddenly return 0 results — the bot
won't crash, but it'll go quiet on that source. The JP scrapers were built in a
sandbox without live access; the **eBay US scraper was built and tested against
the live site (July 2026)** and handles eBay's several page layouts. If anything
errors or returns nothing, copy the terminal output (and any `debug_*.html`
files) back to Claude for a patch.

---

## 8. Files in this folder

| File | What it is |
|---|---|
| `macdeals.py` | the program you run |
| `config.yaml` | **everything you might want to change** — models, prices, fees, thresholds, WhatsApp |
| `pricing.py` / `sources.py` / `store.py` / `report.py` | the bot's internals |
| `requirements.txt` | list of libraries for pip |
| `deals.html` | the dashboard, rewritten after every scan (open it in a browser) |
| `seen_items.db` | memory of already-alerted items (delete to reset) |
| `.swappa_chrome_profile/` | Chrome profile holding Swappa's Cloudflare cookie (delete to reset) |
| `com.macdeals.watch.plist` | what `background start/stop` switches on and off (used in place — don't move it) |
| `macdeals.log` | the background watcher's log (`tail -f` it) |
| `CLOUD_SETUP.md` | run it all in the cloud for free — alerts + dashboard website, laptop off |
| `.github/workflows/scan.yml` | the cloud runner's timetable (used by CLOUD_SETUP) |

Happy hunting! 🎯
