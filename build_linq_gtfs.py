#!/usr/bin/env python3
"""
Build a GTFS feed for The Linq Tunnel Bus from what thelinqtunnelbus.ca shows.

Where each public page gets its data (thelinqtunnelbus.ca is a Base44 app):
  /schedules           -> timetable HARDCODED in the JS bundle (/assets/index-*.js)
  /routes              -> stop list HARDCODED in the JS bundle (same stops as /schedules)
  /special-events-bus  -> live JSON:  /api/apps/<APP_ID>/entities/Event
  /fares               -> live JSON:  /api/apps/<APP_ID>/entities/Fare  (+ SiteSettings)

Note: the API also has a `Schedule` entity, but the public site never displays it
(only the admin panel reads it) and its stops don't match the published timetable,
so it is ignored here.

Usage:  python3 build_linq_gtfs.py [--out linq_gtfs.zip] [--days 365]
Standard library only.
"""
import argparse, csv, datetime as dt, hashlib, io, json, re, sys, urllib.request, zipfile

SITE = "https://thelinqtunnelbus.ca"
APP_ID = "6a8bb8e9959428e6f3199686"
API = f"{SITE}/api/apps/{APP_ID}/entities"
TZ = "America/Toronto"

# ---------------------------------------------------------------------------
# Stops. Every name the site uses must map here, or the build FAILS on purpose.
# ---------------------------------------------------------------------------
STOPS = {
    # stop_id: (stop_name, lat, lon, [names as written on the site])
    "BRUCE": ("Bruce Ave & Riverside Dr W", 42.318323, -83.046351,
              ["Bruce Ave + Riverside Dr W", "Bruce Avenue and Riverside Drive West"]),
    "WASH":  ("Washington Blvd & Jefferson Srv Rd", 42.327633, -83.048202,
              ["Washington Blvd + Jefferson Srv Rd"]),
    "CASS":  ("Cass Ave & Michigan Ave (Rosa Parks Transit Center)", 42.331805, -83.052572,
              ["Cass Ave + Michigan Ave"]),
    "MARC":  ("Tunnel Plaza at Mariners Church", 42.328993, -83.042075,
              ["Tunnel Plaza at Mariners Church"]),
    "MCDOU": ("McDougall St & University Ave E", 42.318320, -83.034185,
              ["McDougall St + University Ave E"]),
    "WITT":  ("Windsor International Transit Terminal", 42.317723, -83.043077,
              ["Windsor International Transit Terminal (WITT)", "300 Chatham St W",
               "Downtown Windsor Terminal"]),
    # Special event drop-off / return pickup stops (red pins on the /routes maps)
    "EVT_A": ("Gratiot Ave at Chrysler Dr (Event Route A)", 42.339732, -83.041905, []),
    "EVT_B": ("W Fisher Service Dr & Cass Ave (Event Route B)", 42.337937, -83.056370, []),
}
ALIAS = {a.lower(): sid for sid, v in STOPS.items() for a in v[3]}

# /routes walking maps: the bus stops once per route; riders walk to the venue.
#   Route A pin (Gratiot Ave)            -> Comerica Park, Ford Field
#   Route B pin (W Fisher Svc Dr & Cass) -> Little Caesars Arena, Fox Theatre, The Fillmore
VENUES = {
    "comerica park": "EVT_A", "ford field": "EVT_A",
    "little caesars arena": "EVT_B", "fox theatre": "EVT_B",
    "the fillmore": "EVT_B", "the fillmore detroit": "EVT_B",
}
EVENT_ROUTE = {"EVT_A": "LINQ-EVT-A", "EVT_B": "LINQ-EVT-B"}
STOP_DESC = {"EVT_A": "Special event stop. Walk to Ford Field or Comerica Park.",
             "EVT_B": "Special event stop. Walk to Little Caesars Arena, Fox Theatre or The Fillmore."}
EVENT_RUN_MINUTES = 40  # events publish only a pickup time; estimated ride to venue

# Column "service" labels on /schedules -> (pickup_type, drop_off_type)
SERVICE = {"pick up": (0, 1), "pick up only": (0, 1), "drop off": (1, 0),
           "drop off only": (1, 0), "pick up & drop": (0, 0), "pick up & drop off": (0, 0)}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "linq-gtfs-builder"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def entity(name):
    return json.loads(get(f"{API}/{name}"))


def stop_id(name):
    sid = ALIAS.get((name or "").strip().lower())
    if not sid:
        sys.exit(f"ERROR: unknown stop/venue on website: {name!r} -> add it to STOPS")
    return sid


def js_array(src, start):
    """Return the JS array literal beginning at src[start] (bracket matching)."""
    depth, i, in_str = 0, start, False
    while i < len(src):
        c = src[i]
        if in_str:
            if c == "\\": i += 1
            elif c == '"': in_str = False
        elif c == '"': in_str = True
        elif c == "[": depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    sys.exit("ERROR: unbalanced array in bundle")


def scrape_timetable():
    """Pull the /schedules timetable and its column headers out of the JS bundle."""
    html = get(f"{SITE}/schedules")
    m = re.search(r'/assets/index-[\w-]+\.js', html)
    if not m:
        sys.exit("ERROR: could not find JS bundle on /schedules")
    js = get(SITE + m.group(0))
    tables = [m.start() for m in re.finditer(r'\[\{day:"', js)]
    if len(tables) != 1:
        sys.exit(f"ERROR: expected 1 timetable in bundle, found {len(tables)} - site layout changed")
    raw = js_array(js, tables[0])
    timetable = json.loads(re.sub(r'([{,])(day|rows):', r'\1"\2":', raw))
    # Column headers: the [{flag:...,name:...,service:...}] array just before it
    cols_at = js.rfind('[{flag:"', 0, tables[0])
    cols = re.findall(r'name:"([^"]+)",service:"([^"]+)"', js_array(js, cols_at))
    if not cols or any(len(r) != len(cols) for t in timetable for r in t["rows"]):
        sys.exit("ERROR: timetable columns don't line up with stop headers")
    return cols, timetable, m.group(0)


def day_flags(label):
    """'Monday to Thursday' / 'Friday to Saturday' / 'Sunday' / 'Sat & Sun' -> 7 flags."""
    flags = [0] * 7
    for part in re.split(r"\s*(?:,|&|and)\s*", label.lower()):
        ends = [d for d in re.split(r"\s*(?:to|-|–)\s*", part) if d]
        idx = []
        for d in ends:
            hit = [i for i, n in enumerate(DAYS) if n.startswith(d[:3])]
            if not hit:
                sys.exit(f"ERROR: can't read service days {label!r}")
            idx.append(hit[0])
        for i in range(idx[0], idx[-1] + 1):
            flags[i] = 1
    return flags


def to_min(t):
    t = t.strip().upper().replace(" ", "")
    fmt = "%I:%M%p" if t.endswith(("AM", "PM")) else "%H:%M"
    x = dt.datetime.strptime(t, fmt)
    return x.hour * 60 + x.minute


def hms(m):
    return f"{m // 60:02d}:{m % 60:02d}:00"


def build(days):
    today = dt.date.today()
    horizon = today + dt.timedelta(days=days)
    cols, timetable, bundle = scrape_timetable()
    settings = (entity("SiteSettings") or [{}])[0]
    fares = [f for f in entity("Fare") if f.get("status") != "expired"]
    events = [e for e in entity("Event")
              if e.get("status") in ("published", "scheduled")
              and today.isoformat() <= e["event_date"][:10] <= horizon.isoformat()
              and not (e.get("archive_at") and e["archive_at"][:10] <= today.isoformat())]

    F = {}
    F["agency"] = [["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang",
                    "agency_phone", "agency_fare_url", "agency_email"],
                   ["LINQ", settings.get("business_name", "The Linq Tunnel Bus"), SITE, TZ, "en",
                    settings.get("phone", ""), f"{SITE}/fares", settings.get("email", "")]]
    F["routes"] = [["route_id", "agency_id", "route_short_name", "route_long_name", "route_type", "route_url"],
                   ["LINQ", "LINQ", "Linq", "Windsor - Detroit Tunnel Bus", 3, f"{SITE}/schedules"],
                   ["LINQ-EVT-A", "LINQ", "Event A", "Special Event Bus: Comerica Park & Ford Field", 3,
                    f"{SITE}/special-events-bus"],
                   ["LINQ-EVT-B", "LINQ", "Event B",
                    "Special Event Bus: Little Caesars Arena, Fox Theatre & The Fillmore", 3,
                    f"{SITE}/special-events-bus"]]

    # ---- regular service (/schedules + /routes) -----------------------------
    col_stops = [stop_id(n) for n, _ in cols]
    col_rules = []
    for _, svc in cols:
        if svc.lower() not in SERVICE:
            sys.exit(f"ERROR: unknown service label {svc!r} - add it to SERVICE")
        col_rules.append(SERVICE[svc.lower()])
    used = set(col_stops)
    F["calendar"] = [["service_id"] + DAYS + ["start_date", "end_date"]]
    F["trips"] = [["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "block_id"]]
    F["stop_times"] = [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
                        "pickup_type", "drop_off_type", "timepoint"]]
    for t in timetable:
        sid = re.sub(r"\W+", "_", t["day"].upper()).strip("_")
        F["calendar"].append([sid] + day_flags(t["day"]) +
                             [today.strftime("%Y%m%d"), horizon.strftime("%Y%m%d")])
        for i, row in enumerate(t["rows"], 1):
            tid, prev = f"{sid}_{i:02d}", -1
            F["trips"].append(["LINQ", sid, tid, STOPS[col_stops[-1]][0], 0, f"{sid}_BUS"])
            for seq, (cell, stp, (pu, do)) in enumerate(zip(row, col_stops, col_rules), 1):
                if not cell.strip():
                    continue
                m = to_min(cell)
                while m < prev:
                    m += 1440            # past midnight -> 24:xx
                prev = m
                F["stop_times"].append([tid, hms(m), hms(m), stp, seq, pu, do, 1])

    # ---- special event buses (/special-events-bus) -------------------------
    F["calendar_dates"] = [["service_id", "date", "exception_type"]]
    for e in sorted(events, key=lambda e: (e["event_date"], e["pickup_time"])):
        d = dt.date.fromisoformat(e["event_date"][:10])
        sid = f"EVT_{d:%Y%m%d}_{e['id'][-6:]}"
        pick = stop_id(e["pickup_location"])
        venue = VENUES.get((e.get("venue") or "").strip().lower())
        if not venue:
            sys.exit(f"ERROR: unknown venue {e.get('venue')!r} - add it to VENUES (route A or B)")
        t0 = to_min(e["pickup_time"])
        F["calendar_dates"].append([sid, d.strftime("%Y%m%d"), 1])
        F["trips"].append([EVENT_ROUTE[venue], sid, f"{sid}_OUT", f"{e['venue']} - {e['title']}", 0, ""])
        F["stop_times"] += [[f"{sid}_OUT", hms(t0), hms(t0), pick, 1, 0, 1, 1],
                            [f"{sid}_OUT", hms(t0 + EVENT_RUN_MINUTES), hms(t0 + EVENT_RUN_MINUTES),
                             venue, 2, 1, 0, 0]]   # timepoint 0 = estimated arrival
        used.update([pick, venue])

    F["stops"] = [["stop_id", "stop_name", "stop_desc", "stop_lat", "stop_lon"]] + \
        [[k, v[0], STOP_DESC.get(k, ""), v[1], v[2]] for k, v in STOPS.items() if k in used]

    # ---- fares (/fares) -----------------------------------------------------
    # Written twice: Fares v2 (fare_media / fare_products / fare_leg_rules with
    # networks) for current consumers, plus Fares v1 (fare_attributes /
    # fare_rules) for older ones. Both describe the same fares.
    F["fare_attributes"] = [["fare_id", "price", "currency_type", "payment_method", "transfers"]]
    F["fare_rules"] = [["fare_id", "route_id"]]
    F["networks"] = [["network_id", "network_name"]]
    F["route_networks"] = [["network_id", "route_id"]]
    F["fare_media"] = [["fare_media_id", "fare_media_name", "fare_media_type"]]
    F["fare_products"] = [["fare_product_id", "fare_product_name", "fare_media_id", "amount", "currency"]]
    F["fare_leg_rules"] = [["leg_group_id", "network_id", "fare_product_id"]]
    networks = {"regular": ("NET_REGULAR", "Regular Scheduled Bus", ["LINQ"]),
                "special_event": ("NET_EVENT", "Special Event Bus", ["LINQ-EVT-A", "LINQ-EVT-B"])}
    media = {}
    for ftype, (net, net_name, rids) in networks.items():
        # Same pick as the /fares page: newest fare of this type already in effect
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        cands = sorted((f for f in fares if f.get("fare_type") == ftype
                        and (f.get("effective_from") or "") <= now),
                       key=lambda f: f.get("effective_from") or "", reverse=True)
        if not cands:
            continue
        f = cands[0]
        price, cur = f"{f['price']:.2f}", f.get("currency", "CAD")
        # v1
        fid = f"FARE_{ftype.upper()}"
        F["fare_attributes"].append([fid, price, cur, 0, 0])
        F["fare_rules"] += [[fid, r] for r in rids]
        # v2: media from the site's payment wording ("debit or credit card only")
        pay = (f.get("payment_methods") or "").lower()
        if "card" in pay or "debit" in pay or "credit" in pay:
            mid, mname, mtype = "CARD_ONBOARD", "Debit or credit card on the bus", 3   # 3 = cEMV
        else:
            mid, mname, mtype = "PAY_ONBOARD", "Pay on the bus", 0                     # 0 = none
        media[mid] = [mid, mname, mtype]
        F["networks"].append([net, net_name])
        F["route_networks"] += [[net, r] for r in rids]
        pid = f"PROD_{ftype.upper()}_ONE_WAY"
        label = f.get("trip_label") or "One Way"
        F["fare_products"].append([pid, f"{f.get('name', net_name)} - {label}", mid, price, cur])
        F["fare_leg_rules"].append([f"LEG_{ftype.upper()}", net, pid])
    F["fare_media"] += list(media.values())
    # No fare_transfer_rules: every boarding is a separate one-way fare.

    digest = hashlib.sha1(json.dumps([cols, timetable, events, fares], sort_keys=True)
                          .encode()).hexdigest()[:10]
    F["feed_info"] = [["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date",
                       "feed_end_date", "feed_version", "feed_contact_email"],
                      [settings.get("business_name", "The Linq Tunnel Bus"), SITE, "en",
                       today.strftime("%Y%m%d"), horizon.strftime("%Y%m%d"), digest,
                       settings.get("email", "")]]
    return F, len(timetable), len(events), bundle


def write_zip(F, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in F.items():
            if len(rows) <= 1 and name not in ("agency", "stops", "routes", "trips", "stop_times", "feed_info"):
                continue
            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerows(rows)
            info = zipfile.ZipInfo(f"{name}.txt", date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, buf.getvalue())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="linq_gtfs.zip")
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args()
    F, nt, ne, bundle = build(a.days)
    write_zip(F, a.out)
    print(f"{a.out}: {nt} service patterns from {bundle}, {ne} event buses, "
          f"{len(F['trips']) - 1} trips, {len(F['stop_times']) - 1} stop_times, version {F['feed_info'][1][5]}")