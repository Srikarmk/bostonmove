# bostonmove

<!-- mcp-name: io.github.Srikarmk/bostonmove -->

<a href="https://github.com/Srikarmk/bostonmove/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Srikarmk/bostonmove/actions/workflows/ci.yml/badge.svg" /></a> <a href="https://pypi.org/project/bostonmove/"><img alt="PyPI" src="https://img.shields.io/pypi/v/bostonmove?color=5b4fc9" /></a> <a href="https://pypi.org/project/bostonmove/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/bostonmove" /></a> <a href="https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.Srikarmk/bostonmove"><img alt="MCP registry" src="https://img.shields.io/badge/MCP%20registry-io.github.Srikarmk%2Fbostonmove-5b4fc9" /></a> <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-blue" /></a>

Getting around Boston from live public data: MBTA departures and service alerts, Bluebikes bike and dock
counts, and door-to-door options: walk, Bluebikes, one MBTA route, or one change between subway lines. A
command-line tool and an MCP server, so Claude Code, Claude Desktop, Cursor and other agents can answer
"when's the next train" or "is there a dock near Kendall" with real numbers. It only reads public data.

```
$ bostonmove plan "Northeastern" "Harvard"
Northeastern University  →  Harvard  (4424 m straight line)
   27 min  Green Line E to Medford/Tufts: board at Northeastern University (0 min walk) now, ride 10, off at Park Street; then Red Line to Alewife: leaves 16:22, ride 9, off at Harvard  (arrive ~16:31)
   28 min  Orange Line to Oak Grove: board at Massachusetts Avenue (6 min walk) in 6 min, ride 6, off at Downtown Crossing; then Red Line to Alewife: leaves 16:20, ride 11, off at Harvard  (arrive ~16:31)
   29 min  Bluebikes from Forsyth St at Huntington Ave (13 bikes) to Church St (1 docks)  (arrive ~16:33)
   40 min  1 to Harvard: board at Massachusetts Ave @ St Botolph St (7 min walk) in 15 min, ride 23, off at Massachusetts Ave opp Holyoke St  (arrive ~16:43)
   69 min  walk, about 5751 m  (arrive ~17:12)
  · Walking and cycling times are estimates from straight-line distance; transit times are from MBTA live predictions, or the timetable when there are none.
  · MBTA options are one route, or one change between subway and light-rail lines with 3 min to change; changes involving buses aren't planned (mbta.com/trip-planner does them).
```

## Install

```bash
uvx bostonmove plan "South Station" "Kendall"   # run once without installing
uv tool install bostonmove                      # or: pipx install bostonmove / pip install bostonmove
```

Add it to Claude Code:

```bash
claude mcp add --scope user bostonmove -- uvx bostonmove mcp
```

Or in any MCP client's config (Claude Desktop, Cursor):

```json
{ "mcpServers": { "bostonmove": { "command": "uvx", "args": ["bostonmove", "mcp"] } } }
```

If the client can't find `uvx`, use the full path that `which uvx` (macOS, Linux) or `where uvx` (Windows)
prints as the `command`.

**MBTA API key (optional, free).** Without one the MBTA allows 20 requests a minute, which two or three trip
plans can use up. With a key from [api-v3.mbta.com](https://api-v3.mbta.com) it's 1,000. Set `MBTA_API_KEY`
in the environment, or pass it in the MCP config with `"env": {"MBTA_API_KEY": "..."}`. When the limit is hit,
bostonmove says so; `plan_trip` still returns the walking and Bluebikes options.

## Tools

| MCP tool | CLI | What it answers |
|---|---|---|
| `next_departures` | `bostonmove next "Park Street" [--route Red]` | Upcoming departures at a stop by route and direction: live when the MBTA has predictions, else the timetable, plus alerts at that stop |
| `service_alerts` | `bostonmove alerts [--route Orange] [--stop X]` | Delays, suspensions, shuttles, detours and closures in effect now, most severe first |
| `bike_availability` | `bostonmove bikes "Kendall" [--need bike\|ebike\|dock]` | Nearest Bluebikes stations with bikes, e-bikes and free docks |
| `find_nearby` | `bostonmove near "Fenway Park"` | MBTA stations and stops, and Bluebikes stations, near a place |
| `plan_trip` | `bostonmove plan A B` | Walk, Bluebikes and MBTA options (one route, or one change between subway lines) with arrival times |

A place can be a station name (`Park Street`, `Kendall`, `Northeastern`), an MBTA stop id (`place-pktrm`,
`110`), a Bluebikes station name, an address or landmark (`Fenway Park`, `360 Huntington Ave`), or `lat,lon`.
Every result carries `sources` and `as_of`. Add `--json` to any CLI command for the full result.

## How the numbers are made

- **Departures** are the MBTA's live predictions. When a stop has none (late at night, or a route that
  doesn't publish them), the timetable is used and each time is marked scheduled.
- **Transit trip times**: wait from the live prediction, ride time from that trip's own predicted times at the
  two stops (or its timetable), walking at each end estimated. A departure you can't walk to in time is skipped.
- **Changes**: when no subway or light-rail line goes the whole way, bostonmove looks for a line near the start
  and a line near the destination that share a station (Park Street, Downtown Crossing, State, Haymarket...),
  ranks the pairs by walking plus stops travelled, and prices the best two with live departures. The second
  train must leave at least 3 minutes after the first gets in.
- **Walking and cycling** are estimates: straight-line distance × 1.3 for streets, at 5 km/h walking and
  14 km/h on a bike. They are labelled `estimate`.
- **Bike counts** are as of the Bluebikes feed time (it refreshes about once a minute); a bike or dock can be
  taken before you get there. In this feed the bike count includes e-bikes, so `classic = bikes - ebikes`.
- **Alerts on a trip** are only the ones that matter for that ride: severity 5 or more on its routes, or any
  alert at a stop where it boards, changes or gets off.

## Limits

- **At most one change, and only between subway and light-rail lines** (Red, Orange, Blue, the Green Line
  branches, Mattapan). Bus to train, bus to bus, commuter rail and two-change trips aren't planned; use
  [mbta.com/trip-planner](https://www.mbta.com/trip-planner) for those.
- A plan costs 4 to 16 MBTA requests. On the keyless limit bostonmove waits up to 20 seconds for the count to
  reset rather than fail; past that it leaves the MBTA options out and says why.
- Stops and bike stations are searched within 800 m of each end of a trip.
- Names are matched to MBTA and Bluebikes stations first, then geocoded with OpenStreetMap inside greater
  Boston. An ambiguous name can land on the wrong place; the result always says what it resolved to.

## Data and credit

- MBTA data from the [MBTA V3 API](https://www.mbta.com/developers/v3-api), under the MassDOT Developers
  License Agreement. bostonmove is not affiliated with or endorsed by the MBTA.
- Bluebikes data from the public [GBFS feed](https://gbfs.bluebikes.com/gbfs/gbfs.json), under the
  [Bluebikes data license](https://www.bluebikes.com/data-license-agreement). Not affiliated with Bluebikes or Lyft.
- Place search by [OpenStreetMap Nominatim](https://nominatim.org/), data © OpenStreetMap contributors
  ([ODbL](https://www.openstreetmap.org/copyright)). Lookups follow its usage policy: at most one a second,
  an identifying User-Agent, and results cached for 30 days.

Slow-changing data (station lists, a route's stop order, geocoded places) is cached on disk for a day in
`~/.cache/bostonmove` (`BOSTONMOVE_CACHE_DIR` to move it, `BOSTONMOVE_NO_CACHE=1` to turn it off). Live data
is cached in memory for under a minute.

## Development

```bash
uv sync
uv run pytest            # offline: replays recorded real responses with the clock frozen
uv run pytest -m live    # against the real APIs
uv run python tests/record_fixtures.py   # re-record the fixtures
```

MIT licence.
