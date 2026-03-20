from __future__ import annotations

import hashlib
import json
import os
import re
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import requests
from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ORIGIN_AIRPORT = "TLV"
ORIGIN_LABEL = "Ben Gurion (Tel Aviv)"
SEARCH_DATES = [
    date(2026, 3, 24),
    date(2026, 3, 25),
    date(2026, 3, 26),
    date(2026, 3, 29),
    date(2026, 3, 30),
]
PREFERRED_DESTINATIONS = [
    ("TBS", "Tbilisi"),
    ("GYD", "Baku"),
    ("BUS", "Batumi"),
    ("EVN", "Yerevan"),
]
FALLBACK_EUROPE_DESTINATIONS = [
    ("ATH", "Athens"),
    ("VIE", "Vienna"),
    ("CDG", "Paris"),
    ("FCO", "Rome"),
    ("BCN", "Barcelona"),
    ("LCA", "Larnaca"),
]
TELEGRAM_TIMEOUT_SECONDS = 30
PAGE_TIMEOUT_MS = 20_000
PAGE_SETTLE_MS = 2_500
TEXT_TIMEOUT_MS = 6_000
STATE_PATH = Path(os.getenv("FLIGHT_STATE_PATH", ".state/flight_state.json"))
MAX_RESULTS_PER_MESSAGE = 12


@dataclass(frozen=True)
class Destination:
    code: str
    city: str


@dataclass(frozen=True)
class SearchProvider:
    name: str
    url_builder: Callable[[str, str, date], str]
    result_markers: tuple[str, ...]
    no_result_markers: tuple[str, ...]


@dataclass(frozen=True)
class Match:
    provider: str
    destination: str
    destination_code: str
    departure_date: str
    source_url: str
    compare_url: str
    is_fallback: bool

    @property
    def signature(self) -> str:
        raw = "|".join(
            [
                self.provider,
                self.destination_code,
                self.departure_date,
                self.source_url,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def skyscanner_url(origin: str, destination: str, departure_date: date) -> str:
    return (
        "https://www.skyscanner.com/transport/flights/"
        f"{origin.lower()}/{destination.lower()}/{departure_date.strftime('%y%m%d')}/"
        "?adultsv2=1&cabinclass=economy&currency=USD&locale=en-US&market=US"
    )


def google_flights_url(origin: str, destination: str, departure_date: date) -> str:
    query = (
        f"Flights from {origin} to {destination} on {departure_date.isoformat()} "
        "one way bag included"
    )
    return f"https://www.google.com/travel/flights?q={requests.utils.quote(query)}"


PROVIDERS = [
    SearchProvider(
        name="Google Flights",
        url_builder=google_flights_url,
        result_markers=("best departing flights", "price graph", "tracked prices", "bags", "stops"),
        no_result_markers=("no flights match", "no flights available", "try another date"),
    ),
    SearchProvider(
        name="Skyscanner",
        url_builder=skyscanner_url,
        result_markers=("select", "book", "price", "depart", "return", "$", "€", "₪"),
        no_result_markers=(
            "no flights found",
            "we couldn't find any flights",
            "there are no available flights",
            "sorry, there are no available flights",
        ),
    ),
]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def load_state() -> set[str]:
    if not STATE_PATH.exists():
        return set()

    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    signatures = data.get("sent_signatures", [])
    return {item for item in signatures if isinstance(item, str)}


def save_state(signatures: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sent_signatures": sorted(signatures)}
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def telegram_send(message: str) -> None:
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=TELEGRAM_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def has_result_markers(page_text: str, provider: SearchProvider) -> bool:
    return any(marker in page_text for marker in provider.result_markers)


def has_no_result_markers(page_text: str, provider: SearchProvider) -> bool:
    return any(marker in page_text for marker in provider.no_result_markers)


def accept_cookies_if_present(page: Page) -> None:
    button_patterns = (
        "accept",
        "agree",
        "got it",
        "allow all",
        "i agree",
    )
    try:
        for pattern in button_patterns:
            locator = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE))
            if locator.count() > 0:
                locator.first.click(timeout=2_000)
                page.wait_for_timeout(1_000)
                return
    except Exception:
        return


def extract_page_text(page: Page) -> str:
    return normalize_text(page.locator("body").inner_text(timeout=TEXT_TIMEOUT_MS))


def create_context(playwright) -> BrowserContext:
    browser = playwright.chromium.launch(headless=True)
    return browser.new_context(
        locale="en-US",
        timezone_id="Europe/Moscow",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
    )


def check_provider(
    page: Page,
    provider: SearchProvider,
    destination: Destination,
    departure_date: date,
    is_fallback: bool,
) -> Match | None:
    url = provider.url_builder(ORIGIN_AIRPORT, destination.code, departure_date)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_SETTLE_MS)
        accept_cookies_if_present(page)
        page_text = extract_page_text(page)
    except (PlaywrightTimeoutError, PlaywrightError):
        return None

    if has_no_result_markers(page_text, provider):
        return None

    if not has_result_markers(page_text, provider):
        return None

    return Match(
        provider=provider.name,
        destination=destination.city,
        destination_code=destination.code,
        departure_date=departure_date.isoformat(),
        source_url=url,
        compare_url=google_flights_url(ORIGIN_AIRPORT, destination.code, departure_date),
        is_fallback=is_fallback,
    )


def collect_results(destinations: list[Destination], is_fallback: bool) -> list[Match]:
    matches: list[Match] = []

    with sync_playwright() as playwright:
        context = create_context(playwright)
        page = context.new_page()

        try:
            for destination in destinations:
                for departure_date in SEARCH_DATES:
                    for provider in PROVIDERS:
                        match = check_provider(page, provider, destination, departure_date, is_fallback)
                        if match is not None:
                            matches.append(match)
        finally:
            context.close()

    return matches


def dedupe_matches(matches: list[Match]) -> list[Match]:
    seen: set[str] = set()
    unique: list[Match] = []
    for match in matches:
        if match.signature in seen:
            continue
        seen.add(match.signature)
        unique.append(match)
    return unique


def filter_new_matches(matches: list[Match], sent_signatures: set[str]) -> list[Match]:
    return [match for match in matches if match.signature not in sent_signatures]


def group_matches(matches: list[Match]) -> tuple[list[Match], list[Match]]:
    preferred = [match for match in matches if not match.is_fallback]
    fallback = [match for match in matches if match.is_fallback]
    return preferred, fallback


def render_match(match: Match) -> str:
    return "\n".join(
        [
        f"- {match.destination} ({match.destination_code}) | {match.departure_date} | {match.provider}",
        f"  Found: {match.source_url}",
        f"  Compare: {match.compare_url}",
        ]
    )


def chunk_matches(matches: list[Match], heading: str) -> list[str]:
    chunks: list[str] = []
    current_lines = [heading]

    for index, match in enumerate(matches, start=1):
        current_lines.append(render_match(match))
        should_flush = index % MAX_RESULTS_PER_MESSAGE == 0 or index == len(matches)
        if should_flush:
            chunks.append("\n".join(current_lines))
            if index != len(matches):
                current_lines = [f"{heading} (continued)"]

    return chunks


def send_matches(matches: list[Match]) -> None:
    preferred, fallback = group_matches(matches)

    messages: list[str] = []
    if preferred:
        messages.extend(chunk_matches(preferred, f"Flights found from {ORIGIN_LABEL}"))
    if fallback:
        messages.extend(
            chunk_matches(
                fallback,
                f"No preferred city found from {ORIGIN_LABEL}; Europe fallback options found",
            )
        )

    for message in messages:
        telegram_send(message)


def main() -> None:
    try:
        sent_signatures = load_state()

        preferred_destinations = [Destination(code, city) for code, city in PREFERRED_DESTINATIONS]
        preferred_matches = dedupe_matches(collect_results(preferred_destinations, is_fallback=False))
        new_preferred_matches = filter_new_matches(preferred_matches, sent_signatures)

        if new_preferred_matches:
            send_matches(new_preferred_matches)
            sent_signatures.update(match.signature for match in new_preferred_matches)
            save_state(sent_signatures)
            return

        fallback_destinations = [Destination(code, city) for code, city in FALLBACK_EUROPE_DESTINATIONS]
        fallback_matches = dedupe_matches(collect_results(fallback_destinations, is_fallback=True))
        new_fallback_matches = filter_new_matches(fallback_matches, sent_signatures)

        if new_fallback_matches:
            send_matches(new_fallback_matches)
            sent_signatures.update(match.signature for match in new_fallback_matches)
            save_state(sent_signatures)
            return

        save_state(sent_signatures)
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
