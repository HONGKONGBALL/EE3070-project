# Choosing the map service

The cane asks one map service for two things: places for a spoken destination, and a walking
route to the chosen place. Which service is a setting, `[nav] provider` in `config/config.toml`.
Turn prompts, re-planning and the K1/K2/K3 flow are the same for all of them.

| `provider` | Covers | Key (environment) | Frame | State |
| --- | --- | --- | --- | --- |
| `amap` (default) | the mainland | `AMAP_KEY` | GCJ-02 | used so far |
| `ors` | Hong Kong, abroad | `ORS_KEY` | WGS-84 | checked live in Hong Kong, see below |
| `google` | Hong Kong, abroad | `GOOGLE_MAPS_KEY` | WGS-84 | written from the API reference, never run against the service |

Why a second service: an AMap key answers error 10045 for any place in Hong Kong, whatever network
the request comes from. It needs AMap's abroad quota.

## openrouteservice

[openrouteservice.org](https://openrouteservice.org) is free to sign up for, without a card. The
key has a daily allowance (about 2000 routes and 1000 name searches a day when this was written;
the account page shows the current numbers). One trip costs one or two searches and one route,
plus one route for every re-plan.

Its data is OpenStreetMap, which shapes the place search:

- **A kind of place** ("最近的地铁站", "the nearest MTR station", "医院", "toilet") goes to the
  category search, which returns what lies within 2 km, nearest first. The kinds that are
  recognised are listed in `CATEGORIES` in `pi/cane/nav/ors.py`.
- **A place by name** ("九龙塘地铁站", "Festival Walk") goes to the name search. Hong Kong names
  are in Traditional characters, so a Simplified query is also tried in its Traditional spelling
  (`pi/cane/nav/hant.py`), and a station also without "地铁".
- Names are tagged in both languages ("九龍塘 Kowloon Tong"). The half that matches the language
  of the navigation prompts (`[navigation] language`) is the one that is spoken.
- Places without a name tag are left out: they cannot be read out.

The search position is rounded to about 1 km (AMap: about 10 km), because the category search
only reaches 2 km. The route request carries the exact position, as it does with AMap.

A step of this service says what to do at its start and then how far to walk. The navigator wants
"walk this stretch, then do that", so each stretch takes the maneuver of the step after it.

## Setting it up on the Pi

1. Put the key next to the other secrets, in `~/.config/smart-cane/psyche.env` (mode 600):
   `ORS_KEY=...`. Never in the repository, a log or a chat.
2. In `config/config.toml`, under `[nav]`, set `provider = "ors"`. `coord_mode` is ignored for a
   service that answers in WGS-84.
3. Restart the camera service.
4. Check the key and the service from the Pi before walking:

   ```sh
   python tools/cane_map_probe.py --provider ors --origin 114.1728,22.3364 --query 地铁站
   ```

   It prints the places found and the route to the first one, and compares every maneuver with
   the turn measured on the route's own geometry.

To go back to AMap, set `provider = "amap"`.

## What was checked, and what was not

Checked on a Mac on 2026-11-24, with a real key, from a start point at City University of Hong Kong:

- Routes: every maneuver agreed with the geometry (`tools/cane_map_probe.py`).
- Search: 地铁站, 最近的地铁站, 医院, 便利店, 图书馆, 九龙塘地铁站, 麦当劳, 香港城市大学, 又一城, and in
  English the nearest MTR station, nearest hospital, convenience store, toilet, library near me,
  Kowloon Tong station, Festival Walk, City University of Hong Kong, McDonald's.
- The cane's own navigation walked along a real route in real time (`tools/cane_route_check.py
  --provider ors --origin 114.1728,22.3364`).

Not checked: anything on the Pi, a walk with a real phone in Hong Kong, how the real Grok dialogue
words its search requests for this service, and the Google adapter against Google.

Known limits: the service is slower than AMap (about a second per request from Shenzhen), and one
request in a few dozen is dropped by the server, so each request is tried twice. A route has no
"crossing" steps, so the cane never asks for a crossing confirmation with this service.
