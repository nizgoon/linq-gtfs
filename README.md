# linq-gtfs

An unofficial [GTFS](https://gtfs.org/) feed for **The Linq Tunnel Bus**, the cross-border bus between Windsor, Ontario and Detroit, Michigan through the Detroit–Windsor Tunnel.

> **Not affiliated with or endorsed by The Linq Tunnel Bus.** This feed is built automatically from the information published on [thelinqtunnelbus.ca](https://thelinqtunnelbus.ca). Always check the official website for the latest schedules, fares and service advisories.

## Feed URL

```
https://raw.githubusercontent.com/nizgoon/linq-gtfs/main/linq_gtfs.zip
```

## What's in the feed

| Route | Service | Source |
|---|---|---|
| **Linq** | Regular scheduled loop: Windsor terminal → Washington Blvd & Jefferson → Cass Ave & Michigan Ave → Tunnel Plaza at Mariners Church → McDougall St & University Ave E → Windsor terminal | [/schedules](https://thelinqtunnelbus.ca/schedules), [/routes](https://thelinqtunnelbus.ca/routes) |
| **Event A** | Special event buses to Comerica Park and Ford Field (stop on Gratiot Ave) | [/special-events-bus](https://thelinqtunnelbus.ca/special-events-bus) |
| **Event B** | Special event buses to Little Caesars Arena, Fox Theatre and The Fillmore (stop on W Fisher Service Dr & Cass Ave) | [/special-events-bus](https://thelinqtunnelbus.ca/special-events-bus) |

The feed also includes:

- **Service patterns** as published: Monday–Thursday, Friday–Saturday and Sunday.
- **Pickup and drop-off rules** for each stop, for example pick-up only at Mariners Church and drop-off only at McDougall St.
- **Fares** in both GTFS Fares v2 (`fare_media`, `fare_products`, `fare_leg_rules`, `networks`) and Fares v1 (`fare_attributes`, `fare_rules`), pulled live from [/fares](https://thelinqtunnelbus.ca/fares).
- **Event buses** for each upcoming event listed on the website, added and removed automatically.

### Terminal move on November 1, 2026

The Linq has announced that its Windsor terminal moves from Bruce Ave & Riverside Dr W to the **Windsor International Transit Terminal (300 Chatham St W)** on November 1, 2026. The feed switches over on that date: trips until October 31 use Bruce Ave, and trips from November 1 start and end at the transit terminal with the same times.

## How it's updated

A GitHub Actions workflow (`.github/workflows/build-gtfs.yml`) runs about every 6 hours. Each run:

1. Reads the current timetable, stops, events and fares from thelinqtunnelbus.ca.
2. Builds `linq_gtfs.zip`.
3. Checks it with the [MobilityData GTFS Validator](https://github.com/MobilityData/gtfs-validator). The run fails and nothing is published if the validator finds any errors.
4. Commits the feed only if it changed.

If the website changes in a way the builder doesn't recognize, such as a new stop name or a new venue, the build stops with an error instead of publishing a broken feed. The last valid feed stays online until the builder is updated.

## Known limitations

- **Event bus arrival times are estimated.** The website lists only a pickup time, so arrival at the event stop is estimated at 40 minutes later and marked as approximate (`timepoint=0`).
- **No event return trips.** Return buses leave after the event ends (45 minutes after the game for sports; posted on the bus for concerts), and end times aren't known in advance.
- **Return time to the Windsor transit terminal is estimated.** From November 1, the arrival back at 300 Chatham St W reuses the published return time to Bruce Ave and is marked as approximate.
- **Fare media type.** Card payment on board is modelled as contactless bank card (`fare_media_type=3`). If the on-board machine works as a regular payment terminal, `0` may be more accurate.
- **Stop coordinates** come from OpenStreetMap and existing DDOT stops, not from the operator.
- **No service changes or holidays** are included unless the website's timetable shows them. Temporary advisories, such as tunnel closures, aren't in the feed.

## Running it locally

Requires Python 3.9+ and nothing else.

```bash
python3 build_linq_gtfs.py --out linq_gtfs.zip
```

Options:

- `--out`: output file (default `linq_gtfs.zip`)
- `--days`: how many days ahead the regular schedule covers (default `365`)

## Maintaining the builder

Most changes on the website are picked up automatically. A few settings near the top of `build_linq_gtfs.py` may need editing:

| Setting | When to change it |
|---|---|
| `STOPS` | A new or renamed stop appears on the website (the build will fail and name it) |
| `VENUES` | A special event at a new venue; assign it to the Event A or Event B stop |
| `EVENT_RUN_MINUTES` | You learn the actual ride time to the event stops |
| `WITT_MOVE` | The terminal move date changes |

## Issues

Spotted something wrong in the feed? [Open an issue](https://github.com/nizgoon/linq-gtfs/issues). For questions about the bus service itself, contact The Linq through [thelinqtunnelbus.ca](https://thelinqtunnelbus.ca).
