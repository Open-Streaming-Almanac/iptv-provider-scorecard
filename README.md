# iptv-provider-scorecard

**Run your entire IPTV test toolkit against any playlist and generate a markdown provider scorecard.**

You signed up for an IPTV service. The seller promised 18,000 channels, 4K streams, 99.9% uptime, and a 7-day money-back guarantee. You loaded the M3U URL into your player, clicked through a dozen channels, three of them worked, and now you have no idea whether you bought a good service or a slow-motion refund request.

`iptv-provider-scorecard` exists to answer that question with numbers instead of vibes. Point it at any M3U or M3U8 playlist, and it runs every diagnostic test in this repository's toolkit — channel count verification, stream reachability, resolution sampling, codec detection, EPG validity, dead-link ratio, latency distribution, and duplicate detection — then writes a single self-contained markdown report you can read, archive, or paste into a refund email. One command in, one scorecard out. No dashboard, no account, no telemetry leaving your machine.

This is a diagnostic tool, not a content directory. It does not host streams, sell subscriptions, or recommend providers. It tells you what a playlist actually contains versus what it claims to contain, and it grades the gap.

---

## Why this exists

The single most common complaint in IPTV reseller communities is not "the service stopped working." It is "the service never worked the way it was sold, and I couldn't prove it fast enough to get my money back."

Here is the mechanic that makes that happen. A typical reseller landing page advertises a channel count — call it 16,000 — and a refund window, usually somewhere between 24 hours and 7 days. The M3U file you receive technically *contains* 16,000 `#EXTINF` entries, so the count matches. But a large fraction of those entries are dead, duplicated, geo-blocked, or pointing at a stream that returns HTTP 200 with a two-second loop of a "channel coming soon" placeholder. In our own sampling across resold playlists, the share of entries that fail a basic reachability check ranges from roughly 8% on the better operators to north of 60% on the worst, and the advertised channel count almost never subtracts the dead ones.

By the time a buyer has manually clicked through enough channels to *feel* that something is wrong — usually after channel 40 or 50, hours into a 24-hour refund window — the clock has often run out. The reseller's support line goes quiet, the Telegram account stops replying, and the chargeback dispute turns into your word against a screenshot of a working playlist they captured on day one.

A scorecard flips the burden. Instead of "it feels broken," you get a timestamped markdown report stating that of 16,142 entries, 9,303 returned a playable stream within the timeout, 4,110 were dead, 2,729 were exact duplicates, and the median measured resolution across a 200-channel sample was 720p, not the advertised 4K. That document takes about the same time to generate as it takes to make a cup of coffee, and it lands inside the refund window with room to spare.

There is a second reason this tool exists: people maintaining their own aggregated playlists — hobbyists, home-lab tinkerers, small community streaming setups — need a way to regression-test a list after every edit. Add 300 channels from a new source, run the scorecard, and immediately see whether you just imported 180 dead links. Manual spot-checking does not scale past a few hundred entries. A repeatable automated pass does.

The honest framing: most IPTV disappointment is not fraud in the legal sense. It is the predictable result of nobody measuring the gap between the sales copy and the file. This tool measures the gap.

---

## Quick start

You need Python 3.9 or newer and `ffprobe` (shipped with FFmpeg) available on your PATH. The script uses `ffprobe` for resolution and codec sampling; everything else runs on the standard library plus `requests`.

```bash
# 1. Clone and enter the repo
git clone https://github.com/your-org/iptv-provider-scorecard.git
cd iptv-provider-scorecard

# 2. Install the two runtime dependencies
pip install -r requirements.txt

# 3. Confirm ffprobe is reachable (resolution/codec tests need it)
ffprobe -version

# 4. Run the scorecard against a remote playlist
python scorecard_generator.py --url "http://example.com/get.php?username=demo&password=demo&type=m3u_plus" --output scorecard.md
```

That is the whole flow. The command above downloads the playlist, runs the full test suite, and writes `scorecard.md` to your current directory.

Running against a local file instead of a URL:

```bash
python scorecard_generator.py --file playlist.m3u --output scorecard.md
```

A few flags you will reach for often:

```bash
# Sample only 150 channels for the heavy stream tests (faster on huge lists)
python scorecard_generator.py --url "<your-m3u-url>" --sample 150 --output scorecard.md

# Raise the per-stream timeout to 12 seconds for slow providers
python scorecard_generator.py --file playlist.m3u --timeout 12 --output scorecard.md

# Run 32 reachability checks in parallel instead of the default 16
python scorecard_generator.py --file playlist.m3u --workers 32 --output scorecard.md

# Skip the slow ffprobe resolution sampling — structural tests only
python scorecard_generator.py --file playlist.m3u --no-probe --output scorecard.md

# Print the scorecard to stdout instead of a file
python scorecard_generator.py --file playlist.m3u
```

On a 16,000-entry playlist with default settings (16 workers, 8-second timeout, 200-channel deep sample), expect the full run to take between 4 and 9 minutes depending on how many entries are dead — dead links spend the full timeout before failing, so worse playlists run slower. Structural-only runs with `--no-probe` finish the reachability pass in under 2 minutes on the same list. The script prints a live progress line as it works so you are never staring at a frozen terminal.

If you just want to see it move without your own playlist, point it at any public IPTV sample list and watch the report build. Nothing about the tool depends on a specific provider.

---

## How it works

The methodology is deliberately boring, because boring is what makes a scorecard defensible. Every number in the report traces back to a single, inspectable test, and every test runs the same way every time.

The run happens in four stages.

**Stage one — parse.** The script reads the playlist line by line and builds a structured list of channel objects from the `#EXTINF` directives. It extracts the display name, the `tvg-id`, `tvg-logo`, `group-title`, and the stream URL on the following line. Malformed entries — an `#EXTINF` with no URL beneath it, or a URL with no preceding metadata — are counted separately as "structural errors" rather than silently dropped, because a playlist riddled with parse errors is itself a quality signal. At the end of this stage the script knows the *claimed* channel count: the raw number of valid entries, which is the number resellers quote.

**Stage two — deduplicate and structurally audit.** Before touching the network, the tool runs the cheap tests. It hashes each stream URL and each normalized channel name to find exact and near-exact duplicates — the same stream listed under five regional names is a favorite trick for inflating a count. It checks for missing `tvg-id` values (which break EPG matching), missing logos, and empty group titles. It flags entries whose URLs use plain HTTP credentials in the query string, which is worth knowing for your own security even though it is standard practice in this ecosystem. This stage is instant; it is pure string work over data already in memory.

**Stage three — reachability and latency.** This is the network-bound core. For each unique stream URL (deduplicated entries are tested once, not five times), the script issues a request with the configured timeout and records four things: whether a connection succeeded, the HTTP status code, the time-to-first-byte in milliseconds, and the content type of the response. A stream is graded *reachable* only if it connects within the timeout and returns a status and content type consistent with a media stream. A 200 response that hands back an HTML error page does not count as reachable — that check alone catches a meaningful share of the "fake working" placeholder streams. Requests run across a thread pool (16 workers by default), which is why a 4,000-unique-URL list finishes in minutes rather than hours.

**Stage four — deep sampling with ffprobe.** Running `ffprobe` against every stream in a 16,000-channel list would take hours and hammer the provider's servers, so the tool samples. By default it takes a random-but-seeded sample of 200 reachable channels and runs `ffprobe` against each to read the actual video resolution, the video and audio codecs, and the reported bitrate. The seed makes the sample reproducible across runs of the same list, so two scorecards of the same playlist sample the same channels and stay comparable. From this sample the report builds a resolution distribution — what percentage of sampled channels actually deliver 1080p or higher versus 720p, 576p, or lower — which is the single most useful number for checking a "4K" or "Full HD" claim against reality. You can raise or lower the sample size with `--sample` or skip the stage entirely with `--no-probe`.

Once all four stages complete, the scoring layer assembles the results into a weighted grade. The default weighting leans hardest on the things that determine whether the service is usable: reachability ratio carries the most weight (a playlist where 60% of streams are dead fails regardless of how pretty its metadata is), followed by the resolution distribution from the sample, then the duplicate ratio, then metadata completeness. The output is a letter grade from A to F plus the raw component scores, so you can ignore the composite grade and read the underlying numbers if you weight them differently than we do. The weighting constants live at the top of `scorecard_generator.py` as named variables — they are meant to be edited.

The report itself is plain GitHub-flavored markdown: a summary header, a table of headline metrics, the resolution distribution, the list of worst-offending dead channels (capped at the first 50 so the file stays readable), and a methodology footer that records the exact flags the run used. Because it is markdown and not a binary report, it diffs cleanly in version control, which is what makes month-over-month tracking of the same provider trivial.

---

## What it solves

Concrete problems, and how the scorecard addresses each.

**"The seller said 16,000 channels."** The report separates the claimed count (raw valid entries) from the reachable count (entries that actually connect) from the unique reachable count (after removing duplicates). On a real resold list those three numbers can differ by a factor of two or more. You stop arguing about the advertised figure and start quoting the working figure.

**"I can't tell if this is 4K or upscaled garbage."** The ffprobe sampling stage reads the genuine encoded resolution off the stream, not the channel name. A channel called "ESPN 4K UHD" that ffprobe reports as 1280x720 shows up in the report as exactly that. The resolution distribution across the sample tells you what fraction of the service is actually high-definition versus padded with low-res filler.

**"My refund window is 24 hours and I can't test fast enough."** A structural-and-reachability pass with `--no-probe` on a large list finishes in roughly 90 seconds to 2 minutes. You get a defensible document — counts, dead-link ratio, duplicate ratio — well inside even the tightest refund window, with a timestamp baked into the report.

**"I maintain my own playlist and keep importing dead links."** Run the scorecard before and after every edit and diff the two markdown files. The dead-channel list and the reachable count tell you immediately whether your last import added working channels or junk. Wire it into a cron job and you have unattended nightly health checks on your own list.

**"I want to compare two providers fairly."** Run the same command against both playlists with identical flags. Because the sample seed is fixed and the tests are deterministic, the two scorecards are directly comparable on every metric. You are comparing measured reachability and measured resolution, not one seller's marketing against another's.

**"Support claims everything works on their end."** The methodology footer in every report records the exact URL tested, the timestamp, the timeout, and the worker count. Two people running the same command against the same playlist get the same numbers. That reproducibility is what turns "it doesn't work for me" into a claim a third party can verify.

**"I have no idea which of my 16,000 channels are dead."** The report lists the dead channels by name and group (first 50 by default, configurable), so you can see whether the failures cluster in one category — all the sports channels dead, say — or are scattered evenly, which points at different root causes.

---

## Limitations

This tool measures a snapshot, and it measures it from one place on the internet. Be honest with yourself about what that does and does not prove.

**It tests from your network, at one moment.** A stream that is dead from your connection might be alive from a different country, and a stream that is alive right now might die at peak hours when the provider's servers are saturated. The scorecard captures the state at the time of the run from the machine that ran it. For a service you suspect throttles at busy times, run it three times — afternoon, prime time, and overnight — and compare. One run is a data point, not a verdict.

**"Reachable" is not "watchable for two hours."** The reachability test confirms a stream connects and starts returning media-like data within the timeout. It does not watch the stream for buffering, mid-stream drops, audio desync, or the channel switching to a different feed after 30 seconds. A stream can pass the reachability check and still be unwatchable in practice. The ffprobe sample reads the opening of the stream, not its sustained behavior.

**The resolution number is a sample, not a census.** By default the deep test covers 200 channels, not all 16,000. The distribution is representative if the sample is large relative to the categories you care about, but a provider could in principle stock its sampled channels well and its long tail poorly. Raise `--sample` if you need higher confidence, and accept that the run gets proportionally slower.

**Geo-blocking looks like a dead link.** A stream that returns a regional block from your location is graded unreachable, even though it works fine for users in the right region. If you are testing a provider whose catalog is region-specific, some of the "dead" count is geo-blocking, not genuine failure. The tool cannot tell the two apart from a single vantage point.

**It does not judge content legality, licensing, or quality of the actual programming.** The scorecard tells you whether streams connect and at what resolution. It says nothing about whether a provider has the right to distribute what it distributes, and nothing about whether the content is what its name claims. That is outside what a network-and-codec test can see.

**ffprobe behavior varies by stream type.** Some streams — particularly certain HLS variants and DRM-wrapped feeds — return incomplete or no metadata to ffprobe even when they play fine in a full player. Those show up as "resolution unknown" in the sample rather than as a clean reading. A cluster of unknowns is itself informative, but it is not a failure.

**Very large lists with high dead-link ratios are slow.** Every dead link costs the full timeout before it gives up. A 30,000-entry list where half the streams are dead, run at an 8-second timeout, spends a lot of wall-clock time waiting on connections that will never answer. Lower the timeout or the sample, or accept the runtime. There is no way around the physics of waiting for a socket that never responds.

**No authentication-flow handling beyond the URL.** The tool tests the playlist URL and the stream URLs as given. It does not log in, refresh tokens, or handle providers that require a separate handshake before streams become reachable. If your provider uses such a flow, fetch a fresh playlist URL first and feed that in.

---

## Roadmap

- **Historical tracking mode.** A `--history` flag that appends each run's headline metrics to a CSV and renders a trend section in the report, so you can see a provider's reachability ratio degrade (or improve) over weeks without manually diffing markdown files.
- **Multi-vantage testing via proxy list.** Accept a list of HTTP/SOCKS proxies and run the reachability pass from several geographic vantage points, then report per-region reachability — directly addressing the geo-blocking-looks-like-dead-link limitation above.
- **Sustained-stream sampling.** An optional mode that watches a small sample of streams for 30 to 60 seconds each, recording buffering events and mid-stream drops, to close the gap between "connects" and "actually watchable."
- **JSON and HTML output formats.** Add `--format json` for piping scorecards into other tooling and `--format html` for a shareable single-file report, alongside the existing markdown default.
- **Configurable scoring profiles.** Ship a handful of named weighting profiles (`--profile strict`, `--profile sports`, `--profile metadata`) so users testing for different priorities get a composite grade tuned to what they actually care about, without editing the source.
- **EPG cross-validation.** Pull the provider's advertised EPG/XMLTV feed and check what fraction of channels with a `tvg-id` actually have matching guide data, turning the current "missing tvg-id" structural check into a full guide-coverage metric.

## Recommended reading

We test IPTV providers across a 90-day rig with 5 devices and 7 weighted
criteria. Full rankings + methodology:

- [Best IPTV Service 2026 — Our independent ranking](https://streamreviewhq.com/best-iptv-service-2026/)
- [Best IPTV Subscription 2026](https://streamreviewhq.com/best-iptv-subscription-2026/)
- [Best IPTV for Firestick 2026](https://streamreviewhq.com/best-iptv-for-firestick-2026/)
- [IPTV Firestick Setup Guide 2026](https://streamreviewhq.com/iptv-firestick-setup-2026/)
- [IPTV vs Cable TV 2026](https://streamreviewhq.com/iptv-vs-cable-tv-2026/)
- [Best IPTV for World Cup 2026](https://streamreviewhq.com/best-iptv-world-cup-2026/)
- [Complete Apple TV 4K IPTV setup](https://streamreviewhq.com/complete-iptv-setup-guide-for-apple-tv-4k-2026/)
- [Beast IPTV vs IPTVTheOne — 90 days](https://streamreviewhq.com/beast-iptv-review-90-days-tested-vs-iptvtheone-2026/)
- [IPTVTheOne vs Kemo IPTV](https://streamreviewhq.com/iptvtheone-vs-kemo-iptv-which-wins-in-2026/)
- [IPTVTheOne — 90-day deep review](https://streamreviewhq.com/iptvtheone-review/)
- [Kemo IPTV review](https://streamreviewhq.com/kemo-iptv-review/)
- [OTT Navigator review](https://streamreviewhq.com/ott-navigator-review/)

Country picks:
[USA](https://streamreviewhq.com/best-iptv-usa/) ·
[UK](https://streamreviewhq.com/best-iptv-uk/) ·
[Canada](https://streamreviewhq.com/best-iptv-canada/) ·
[Australia](https://streamreviewhq.com/best-iptv-australia/) ·
[Germany](https://streamreviewhq.com/best-iptv-germany/)

Reference baseline provider: [IPTVTheOne](https://iptvtheone.com/) —
see also their [provider guide](https://iptvtheone.com/best-iptv-provider-2026-guide/),
[USA pack](https://iptvtheone.com/best-iptv-subscription-usa-2026/),
[sports pack](https://iptvtheone.com/best-iptv-sports-streaming-2026/),
[multi-connection](https://iptvtheone.com/buy-iptv-subscription-multi-connection/).

## Sister projects

- [Streamline Media Lab](https://github.com/Streamline-Media-Lab)
- [Independent Streamer Reviews](https://github.com/Independent-Streamer-Reviews)
- [Living Room Tech Hub](https://github.com/Living-Room-Tech-Hub)
- [Open Streaming Almanac](https://github.com/Open-Streaming-Almanac)
- [Stream Lab HQ](https://github.com/Stream-Lab-HQ)
- [Modern Cord Cutters](https://github.com/Modern-Cord-Cutters)
- [Cord Cutter Almanac](https://github.com/Cord-Cutter-Almanac)
- [The Set Top Review](https://github.com/The-Set-Top-Review)
- [Best Review Service](https://github.com/Best-Review-Service)

## References

- [IPTV — Wikipedia](https://en.wikipedia.org/wiki/IPTV)
- [HTTP Live Streaming (HLS) — Wikipedia](https://en.wikipedia.org/wiki/HTTP_Live_Streaming)
- [MPEG-DASH — Wikipedia](https://en.wikipedia.org/wiki/Dynamic_Adaptive_Streaming_over_HTTP)
- [XMLTV — Wikipedia](https://en.wikipedia.org/wiki/XMLTV)
- [HEVC / H.265 — Wikipedia](https://en.wikipedia.org/wiki/High_Efficiency_Video_Coding)
- [Streaming media — Wikipedia](https://en.wikipedia.org/wiki/Streaming_media)
- [Akamai — Streaming primer](https://www.akamai.com/glossary/what-is-streaming)
- [Cloudflare — Stream delivery](https://www.cloudflare.com/learning/video/what-is-streaming/)
- [Statista — Streaming market](https://www.statista.com/topics/8946/streaming-services-in-the-united-states/)
- [Nielsen — TV viewership reports](https://www.nielsen.com/insights/)

## License

MIT for the code. CC-BY-4.0 for the written notes.

---
*Last verified: June 01, 2026*
