#!/usr/bin/env python3
"""Automated provider scorecard generator.

Runs every test in our toolkit against a given IPTV playlist (M3U/M3U8) and
produces a structured markdown report plus a machine-readable JSON summary.
Each test inspects the parsed playlist for a quality / hygiene signal, scores
it 0-100, and the weighted aggregate becomes the provider's letter grade.

stdlib only, Python 3.10+. Optional live reachability probing via --probe.

More IPTV provider reviews & scorecards: https://streamreviewhq.com/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Channel:
    name: str
    url: str
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class TestResult:
    name: str
    score: float          # 0-100
    weight: float         # relative importance
    detail: str


def log(msg: str, verbose: bool) -> None:
    if verbose:
        print(f"[scorecard] {msg}", file=sys.stderr)


def parse_m3u(text: str) -> list[Channel]:
    """Parse an extended M3U into Channel objects."""
    channels: list[Channel] = []
    pending: dict[str, str] | None = None
    pending_name = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            pending_name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
            pending = attrs
        elif line.startswith("#"):
            continue
        else:
            channels.append(Channel(pending_name or "(unnamed)", line, pending or {}))
            pending, pending_name = None, ""
    return channels


# --- Individual toolkit tests -------------------------------------------------

def test_header(text: str, chans: list[Channel]) -> TestResult:
    ok = text.lstrip().upper().startswith("#EXTM3U")
    return TestResult("valid_m3u_header", 100 if ok else 0, 1.0,
                      "Has #EXTM3U header" if ok else "Missing #EXTM3U header")


def test_channel_count(text: str, chans: list[Channel]) -> TestResult:
    n = len(chans)
    score = 0 if n == 0 else min(100, 40 + n // 10)
    return TestResult("channel_count", score, 1.0, f"{n} channels parsed")


def test_https_ratio(text: str, chans: list[Channel]) -> TestResult:
    if not chans:
        return TestResult("https_ratio", 0, 1.5, "No channels")
    https = sum(1 for c in chans if c.url.lower().startswith("https://"))
    pct = 100 * https / len(chans)
    return TestResult("https_ratio", pct, 1.5, f"{https}/{len(chans)} streams use HTTPS")


def test_duplicate_urls(text: str, chans: list[Channel]) -> TestResult:
    if not chans:
        return TestResult("duplicate_urls", 0, 1.0, "No channels")
    urls = [c.url for c in chans]
    dupes = len(urls) - len(set(urls))
    pct = 100 * (1 - dupes / len(urls))
    return TestResult("duplicate_urls", pct, 1.0, f"{dupes} duplicate stream URLs")


def test_metadata_logos(text: str, chans: list[Channel]) -> TestResult:
    if not chans:
        return TestResult("logo_coverage", 0, 0.8, "No channels")
    with_logo = sum(1 for c in chans if c.attrs.get("tvg-logo"))
    pct = 100 * with_logo / len(chans)
    return TestResult("logo_coverage", pct, 0.8, f"{with_logo}/{len(chans)} channels have logos")


def test_group_titles(text: str, chans: list[Channel]) -> TestResult:
    if not chans:
        return TestResult("group_coverage", 0, 0.8, "No channels")
    grouped = sum(1 for c in chans if c.attrs.get("group-title"))
    pct = 100 * grouped / len(chans)
    return TestResult("group_coverage", pct, 0.8, f"{grouped}/{len(chans)} channels categorized")


def test_epg_ids(text: str, chans: list[Channel]) -> TestResult:
    if not chans:
        return TestResult("epg_coverage", 0, 1.0, "No channels")
    with_id = sum(1 for c in chans if c.attrs.get("tvg-id"))
    pct = 100 * with_id / len(chans)
    return TestResult("epg_coverage", pct, 1.0, f"{with_id}/{len(chans)} channels have EPG tvg-id")


def test_url_scheme_sanity(text: str, chans: list[Channel]) -> TestResult:
    if not chans:
        return TestResult("url_validity", 0, 1.2, "No channels")
    valid = sum(1 for c in chans if re.match(r"^[a-zA-Z]+://[^\s]+$", c.url))
    pct = 100 * valid / len(chans)
    return TestResult("url_validity", pct, 1.2, f"{valid}/{len(chans)} URLs well-formed")


def test_reachability(chans: list[Channel], sample: int, timeout: float,
                      verbose: bool) -> TestResult:
    """Live HEAD/GET probe of a sample of streams."""
    targets = [c for c in chans if c.url.lower().startswith(("http://", "https://"))][:sample]
    if not targets:
        return TestResult("stream_reachability", 0, 2.0, "No HTTP streams to probe")
    alive = 0
    for c in targets:
        try:
            req = urllib.request.Request(c.url, method="GET", headers={"User-Agent": "scorecard/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status < 400:
                    alive += 1
            log(f"probe OK {c.name}", verbose)
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as e:
            log(f"probe FAIL {c.name}: {e}", verbose)
    pct = 100 * alive / len(targets)
    return TestResult("stream_reachability", pct, 2.0,
                      f"{alive}/{len(targets)} sampled streams responded")


def grade(score: float) -> str:
    for cutoff, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= cutoff:
            return letter
    return "F"


def build_markdown(provider: str, results: list[TestResult], overall: float,
                   chan_count: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# IPTV Provider Scorecard — {provider}",
        "",
        f"**Generated:** {ts}  ",
        f"**Channels analyzed:** {chan_count}  ",
        f"**Overall score:** {overall:.1f}/100 → **Grade {grade(overall)}**",
        "",
        "| Test | Score | Weight | Notes |",
        "|------|------:|-------:|-------|",
    ]
    for r in results:
        lines.append(f"| {r.name} | {r.score:.0f} | {r.weight:g} | {r.detail} |")
    lines += [
        "",
        "---",
        "More IPTV provider reviews & scorecards: https://streamreviewhq.com/",
        "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    log(f"reading {args.input}", args.verbose)
    try:
        with open(args.input, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        print(f"error: cannot read input: {e}", file=sys.stderr)
        return 2

    chans = parse_m3u(text)
    log(f"parsed {len(chans)} channels", args.verbose)

    results = [
        test_header(text, chans),
        test_channel_count(text, chans),
        test_https_ratio(text, chans),
        test_duplicate_urls(text, chans),
        test_metadata_logos(text, chans),
        test_group_titles(text, chans),
        test_epg_ids(text, chans),
        test_url_scheme_sanity(text, chans),
    ]
    if args.probe:
        log(f"probing up to {args.sample} streams", args.verbose)
        results.append(test_reachability(chans, args.sample, args.timeout, args.verbose))

    total_w = sum(r.weight for r in results) or 1.0
    overall = sum(r.score * r.weight for r in results) / total_w

    provider = args.provider or args.input
    md = build_markdown(provider, results, overall, len(chans))
    try:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
    except OSError as e:
        print(f"error: cannot write report: {e}", file=sys.stderr)
        return 2
    log(f"report written to {args.output}", args.verbose)

    summary = {
        "provider": provider,
        "channels": len(chans),
        "overall_score": round(overall, 1),
        "grade": grade(overall),
        "report": args.output,
        "tests": [
            {"name": r.name, "score": round(r.score, 1), "weight": r.weight, "detail": r.detail}
            for r in results
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Run the full test toolkit against an IPTV playlist and "
                    "generate a markdown provider scorecard.")
    p.add_argument("-i", "--input", required=True, help="path to .m3u / .m3u8 playlist")
    p.add_argument("-o", "--output", default="scorecard.md", help="markdown report path")
    p.add_argument("-p", "--provider", help="provider name for the report header")
    p.add_argument("--probe", action="store_true", help="live-probe a sample of streams")
    p.add_argument("--sample", type=int, default=15, help="streams to probe with --probe")
    p.add_argument("--timeout", type=float, default=8.0, help="per-stream probe timeout (s)")
    p.add_argument("-v", "--verbose", action="store_true", help="verbose progress on stderr")
    args = p.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
