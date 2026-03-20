from __future__ import annotations

import hashlib
import json
import os
import re
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ORIGIN_AIRPORT = "TLV"
ORIGIN_LABEL_RU = "Бен-Гурион"
SEARCH_DATES = [
    date(2026, 3, 24),
    date(2026, 3, 25),
    date(2026, 3, 26),
    date(2026, 3, 29),
    date(2026, 3, 30),
]
PREFERRED_DESTINATIONS = [
    ("TBS", "Тбилиси"),
    ("GYD", "Баку"),
    ("BUS", "Батуми"),
    ("EVN", "Ереван"),
]
FALLBACK_EUROPE_DESTINATIONS = [
    ("ATH", "Афины"),
    ("VIE", "Вена"),
    ("CDG", "Париж"),
    ("FCO", "Рим"),
    ("BCN", "Барселона"),
    ("LCA", "Ларнака"),
]
TELEGRAM_TIMEOUT_SECONDS = 30
PAGE_TIMEOUT_MS = 20_000
PAGE_SETTLE_MS = 5_000
TEXT_TIMEOUT_MS = 6_000
STATE_PATH = Path(os.getenv("FLIGHT_STATE_PATH", ".state/flight_state.json"))
MAX_RESULTS_PER_MESSAGE = 8


@dataclass(frozen=True)
class Destination:
    code: str
    city: str


@dataclass(frozen=True)
class Match:
    provider: str
    destination: str
    destination_code: str
    departure_date: str
    departure_time: str
    airline: str
    booking_url: str
    price_text: str | None
    is_fallback: bool

    @property
    def signature(self) -> str:
        raw = "|".join(
            [
                self.provider,
                self.destination_code,
                self.departure_date,
                self.departure_time,
                self.booking_url,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def skyscanner_url(origin: str, destination: str, departure_date: date) -> str:
    return (
        "https://www.skyscanner.com/transport/flights/"
        f"{origin.lower()}/{destination.lower()}/{departure_date.strftime('%y%m%d')}/"
        "?adultsv2=1&cabinclass=economy&currency=USD&locale=en-US&market=US"
    )


NO_RESULT_MARKERS = (
    "no flights found",
    "we couldn't find any flights",
    "there are no available flights",
    "sorry, there are no available flights",
    "no flights available",
)


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


def has_result_markers(page_text: str) -> bool:
    return "select" in page_text or "book" in page_text or "price" in page_text


def has_no_result_markers(page_text: str) -> bool:
    return any(marker in page_text for marker in NO_RESULT_MARKERS)


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


def walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_json(item)


def first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def format_time(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{2}:\d{2})", value)
    if match:
        return match.group(1)
    return None


def format_price(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        amount = value.get("amount") or value.get("price") or value.get("formatted")
        currency = value.get("currency") or value.get("unit")
        if amount and currency:
            return f"{amount} {currency}"
        if amount:
            return str(amount)
    return None


def is_useful_booking_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("http"):
        return False
    host = urlparse(value).netloc.lower()
    if not host:
        return False
    if "google.com" in host and "/travel/flights" in value:
        return False
    return True


def find_booking_url(value: Any) -> str | None:
    if isinstance(value, dict):
        direct_candidates = [
            value.get("deepLink"),
            value.get("deep_link"),
            value.get("deeplink"),
            value.get("bookingUrl"),
            value.get("booking_url"),
            value.get("url"),
            value.get("link"),
        ]
        for candidate in direct_candidates:
            if is_useful_booking_url(candidate):
                return candidate
        for item in value.values():
            found = find_booking_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_booking_url(item)
            if found:
                return found
    elif is_useful_booking_url(value):
        return value
    return None


def find_first_mapping_with_keys(payloads: list[dict[str, Any]], required_keys: set[str]) -> dict[str, Any] | None:
    for payload in payloads:
        for item in walk_json(payload):
            if required_keys.issubset(item.keys()):
                return item
    return None


def carrier_name_from_leg(leg: dict[str, Any], carriers_by_id: dict[str, Any]) -> str:
    carrier_ids: list[Any] = []
    carriers = leg.get("carriers")
    if isinstance(carriers, dict):
        for key in ("marketing", "operating"):
            maybe_items = carriers.get(key)
            if isinstance(maybe_items, list):
                for item in maybe_items:
                    if isinstance(item, dict):
                        carrier_ids.append(item.get("id") or item.get("code"))
                    else:
                        carrier_ids.append(item)
    if not carrier_ids:
        for key in ("marketingCarrierIds", "carrierIds", "carriers"):
            maybe_items = leg.get(key)
            if isinstance(maybe_items, list):
                carrier_ids.extend(maybe_items)

    names: list[str] = []
    for carrier_id in carrier_ids:
        carrier = carriers_by_id.get(str(carrier_id)) or carriers_by_id.get(carrier_id)
        if isinstance(carrier, dict):
            name = first_non_empty_string(
                carrier.get("name"),
                carrier.get("displayCode"),
                carrier.get("code"),
            )
            if name and name not in names:
                names.append(name)
        elif isinstance(carrier_id, str) and carrier_id not in names:
            names.append(carrier_id)

    return ", ".join(names) if names else "Авиакомпания не указана"


def parse_skyscanner_payloads(
    payloads: list[dict[str, Any]],
    destination: Destination,
    departure_date: date,
    is_fallback: bool,
) -> list[Match]:
    match_container = find_first_mapping_with_keys(payloads, {"itineraries", "legs"})
    if not match_container:
        return []

    itineraries_raw = match_container.get("itineraries")
    if isinstance(itineraries_raw, dict):
        itineraries = list(itineraries_raw.values())
    elif isinstance(itineraries_raw, list):
        itineraries = itineraries_raw
    else:
        return []

    legs_raw = match_container.get("legs")
    carriers_raw = match_container.get("carriers", {})
    if not isinstance(legs_raw, dict):
        return []

    carriers_by_id: dict[str, Any] = {}
    if isinstance(carriers_raw, dict):
        carriers_by_id = {str(key): value for key, value in carriers_raw.items()}

    matches: list[Match] = []
    for itinerary in itineraries:
        if not isinstance(itinerary, dict):
            continue

        leg_ids = itinerary.get("legIds")
        if not isinstance(leg_ids, list) or not leg_ids:
            outbound_leg_id = itinerary.get("outboundLegId")
            leg_ids = [outbound_leg_id] if outbound_leg_id else []
        if not leg_ids:
            continue

        leg = legs_raw.get(leg_ids[0])
        if not isinstance(leg, dict):
            continue

        departure_time = format_time(
            first_non_empty_string(
                leg.get("departure"),
                leg.get("departureDateTime"),
                leg.get("departureTime"),
                leg.get("localDeparture"),
            )
        )
        if not departure_time:
            continue

        pricing_candidates = itinerary.get("pricingOptions") or itinerary.get("pricing_options") or []
        if not isinstance(pricing_candidates, list):
            pricing_candidates = [pricing_candidates]

        booking_url = find_booking_url(pricing_candidates) or find_booking_url(itinerary)
        if not booking_url:
            continue

        price_text = None
        for option in pricing_candidates:
            if isinstance(option, dict):
                price_text = format_price(option.get("price") or option.get("amount") or option)
                if price_text:
                    break

        matches.append(
            Match(
                provider="Skyscanner",
                destination=destination.city,
                destination_code=destination.code,
                departure_date=departure_date.isoformat(),
                departure_time=departure_time,
                airline=carrier_name_from_leg(leg, carriers_by_id),
                booking_url=booking_url,
                price_text=price_text,
                is_fallback=is_fallback,
            )
        )

        if len(matches) >= 3:
            break

    return matches


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


def check_skyscanner(
    page: Page,
    destination: Destination,
    departure_date: date,
    is_fallback: bool,
) -> list[Match]:
    url = skyscanner_url(ORIGIN_AIRPORT, destination.code, departure_date)
    payloads: list[dict[str, Any]] = []

    def handle_response(response) -> None:
        try:
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower():
                return
            data = response.json()
            if isinstance(data, dict):
                payloads.append(data)
        except Exception:
            return

    try:
        page.on("response", handle_response)
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_SETTLE_MS)
        accept_cookies_if_present(page)
        page_text = extract_page_text(page)
    except (PlaywrightTimeoutError, PlaywrightError):
        return []

    if has_no_result_markers(page_text):
        return []

    if not has_result_markers(page_text):
        return []

    return parse_skyscanner_payloads(payloads, destination, departure_date, is_fallback)


def collect_results(destinations: list[Destination], is_fallback: bool) -> list[Match]:
    matches: list[Match] = []
    phase = "fallback" if is_fallback else "preferred"

    with sync_playwright() as playwright:
        context = create_context(playwright)
        page = context.new_page()

        try:
            for destination in destinations:
                for departure_date in SEARCH_DATES:
                    print(
                        f"[flight-watch] phase={phase} destination={destination.code} "
                        f"date={departure_date.isoformat()} provider=Skyscanner",
                        flush=True,
                    )
                    matches.extend(check_skyscanner(page, destination, departure_date, is_fallback))
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
    lines = [
        match.departure_time,
        f"{ORIGIN_LABEL_RU} - {match.destination}",
    ]
    lines.append(match.booking_url)
    return "\n".join(lines)


def russian_date_label(iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"{day}.{month}"


def chunk_matches(matches: list[Match], heading: str) -> list[str]:
    matches = sorted(matches, key=lambda item: (item.departure_date, item.departure_time, item.destination))
    chunks: list[str] = []
    current_lines = [heading]
    current_count = 0
    current_date = None

    for match in matches:
        if current_count >= MAX_RESULTS_PER_MESSAGE:
            chunks.append("\n\n".join(current_lines))
            current_lines = [heading]
            current_count = 0
            current_date = None

        if current_date != match.departure_date:
            current_date = match.departure_date
            current_lines.append(russian_date_label(match.departure_date))

        current_lines.append(render_match(match))
        current_count += 1

    if len(current_lines) > 1:
        chunks.append("\n\n".join(current_lines))
    return chunks


def send_matches(matches: list[Match]) -> None:
    preferred, fallback = group_matches(matches)

    messages: list[str] = []
    if preferred:
        messages.extend(chunk_matches(preferred, "Найдены билеты на следующие даты:"))
    if fallback:
        messages.extend(
            chunk_matches(
                fallback,
                "По приоритетным городам ничего не найдено, но есть варианты по Европе:",
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
