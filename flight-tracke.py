import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests
import schedule
from dotenv import load_dotenv

load_dotenv()

# Keep the SerpAPI key in Replit Secrets or a local .env file.
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GMAIL_SEND_SCRIPT = Path(__file__).with_name("gmail_sender.mjs")
ALERT_STATE_FILE = Path(__file__).with_name(".flight_alert_state.json")
CHECK_INTERVAL_MINUTES = 30

# Your round-trip flights
ROUND_TRIP = [
    {
        "from": "AMD",
        "to": "COK",
        "date": "2027-02-13",
        "name": "Outbound (AMD → COK)",
    },
    {
        "from": "COK",
        "to": "AMD",
        "date": "2027-02-20",
        "name": "Return (COK → AMD)",
    },
]

ROUND_TRIP_BUDGET_MIN = 10000
ROUND_TRIP_BUDGET_MAX = 13000


def load_last_alerted_prices():
    """Load the last fare that produced a successfully sent alert."""
    if not ALERT_STATE_FILE.exists():
        return None

    try:
        with ALERT_STATE_FILE.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Could not read the saved flight alert state.") from error

    if not isinstance(state, dict) or "last_alerted_prices" not in state:
        raise ValueError("The saved flight alert state has an invalid format.")

    prices = state["last_alerted_prices"]
    if prices is None:
        return None

    if (
        not isinstance(prices, dict)
        or set(prices) != {"outbound", "return"}
        or any(type(price) is not int for price in prices.values())
    ):
        raise ValueError("The saved flight alert state has invalid prices.")

    return prices


def save_last_alerted_prices(prices):
    """Atomically save the last alerted fare, or None to re-arm alerts."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=ALERT_STATE_FILE.parent,
            prefix=f"{ALERT_STATE_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as state_file:
            temporary_path = Path(state_file.name)
            json.dump({"last_alerted_prices": prices}, state_file)
            state_file.write("\n")

        os.replace(temporary_path, ALERT_STATE_FILE)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise RuntimeError("Could not save the flight alert state.") from error


def send_email(subject, body, total_price, prices):
    """Send an email alert."""
    email_body = f"""
✈️ ROUND TRIP FLIGHT PRICE ALERT! ✈️

Great news! Your round trip is now within budget!
{body}

📍 Trip: Ahmedabad ↔ Kochi
📅 Outbound: Feb 13, 2027
📅 Return: Feb 20, 2027

💰 Price Breakdown:
   • Outbound (AMD → COK): ₹{prices['outbound']}
   • Return (COK → AMD): ₹{prices['return']}
   ───────────────────────────────
   • TOTAL ROUND TRIP: ₹{total_price}

💵 Your Budget: ₹{ROUND_TRIP_BUDGET_MIN} - ₹{ROUND_TRIP_BUDGET_MAX}
✅ Status: WITHIN BUDGET!

🔗 Book now on Google Flights before prices go up!
https://www.google.com/flights

Checked at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """

    try:
        result = subprocess.run(
            ["node", str(GMAIL_SEND_SCRIPT)],
            input=json.dumps({"subject": subject, "body": email_body}),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or "Gmail helper exited with an error."
            raise RuntimeError(details)

        print(f"Email sent to the connected Gmail account. Total: ₹{total_price}")
        return True
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print(f"Email error: {error}")
        return False


def get_flight_price(flight):
    """Get a flight price from SerpAPI."""
    try:
        response = requests.get(
            "https://serpapi.com/search",
            params={
                "engine": "google_flights",
                "type": 2,
                "departure_id": flight["from"],
                "arrival_id": flight["to"],
                "outbound_date": flight["date"],
                "currency": "INR",
                "api_key": SERPAPI_KEY,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        best_flights = data.get("best_flights", [])
        if best_flights:
            price = best_flights[0].get("price")
            if isinstance(price, str):
                price = price.replace("₹", "").replace(",", "").strip()
            if price is not None:
                return int(price)

        print(f"No price found for {flight['name']}.")
        return None
    except requests.RequestException as error:
        status_code = getattr(error.response, "status_code", None)
        if status_code is None:
            detail = "network request failed"
        else:
            detail = f"HTTP {status_code}"
        print(f"Error fetching {flight['name']}: {detail}.")
        return None
    except (ValueError, KeyError, TypeError):
        print(f"Could not parse the price response for {flight['name']}.")
        return None


def check_flights():
    """Check both flight prices and compare their total with the budget."""
    print(f"\n{'=' * 70}")
    print(f"Checking flights at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 70}\n")

    outbound_price = None
    return_price = None

    for flight in ROUND_TRIP:
        price = get_flight_price(flight)
        if flight["name"].startswith("Outbound"):
            outbound_price = price
        else:
            return_price = price

    if outbound_price is not None and return_price is not None:
        total_price = outbound_price + return_price

        print(f"Outbound (AMD → COK, Feb 13): ₹{outbound_price}")
        print(f"Return (COK → AMD, Feb 20): ₹{return_price}")
        print(f"{'─' * 70}")
        print(f"TOTAL ROUND TRIP: ₹{total_price}")
        print(
            f"Target budget: ₹{ROUND_TRIP_BUDGET_MIN} - "
            f"₹{ROUND_TRIP_BUDGET_MAX}\n"
        )

        current_prices = {
            "outbound": outbound_price,
            "return": return_price,
        }
        last_alerted_prices = load_last_alerted_prices()

        if ROUND_TRIP_BUDGET_MIN <= total_price <= ROUND_TRIP_BUDGET_MAX:
            if current_prices == last_alerted_prices:
                print("This fare was already alerted. No email sent.")
            else:
                print("Price is within budget. Sending an email alert.")
                sent = send_email(
                    subject=f"Round Trip Alert: AMD ↔ COK @ ₹{total_price}!",
                    body=f"Total round trip is ₹{total_price}!",
                    total_price=total_price,
                    prices=current_prices,
                )
                if sent:
                    save_last_alerted_prices(current_prices)
        elif total_price > ROUND_TRIP_BUDGET_MAX:
            if last_alerted_prices is not None:
                save_last_alerted_prices(None)
            print(
                f"Too expensive. Total is ₹{total_price}; "
                f"target is ₹{ROUND_TRIP_BUDGET_MAX} or less."
            )
        else:
            if last_alerted_prices is not None:
                save_last_alerted_prices(None)
            print(f"Total is below your budget: ₹{total_price}")
    else:
        print("Could not fetch one or both flight prices.")
        if outbound_price is not None:
            print(f"Outbound: ₹{outbound_price}")
        if return_price is not None:
            print(f"Return: ₹{return_price}")

    print()


def main():
    required_settings = {
        "SERPAPI_KEY": SERPAPI_KEY,
    }
    missing_settings = [
        name for name, value in required_settings.items() if not value
    ]

    if missing_settings:
        print("Missing required settings: " + ", ".join(missing_settings))
        print("Add the key in Replit Secrets, or use a local .env file.")
        return

    print(
        f"""
╔════════════════════════════════════════════════════════════════════╗
║                 ROUND TRIP FLIGHT TRACKER                         ║
║                                                                    ║
║  Trip: Ahmedabad ↔ Kochi                                          ║
║  Outbound: Feb 13, 2027                                           ║
║  Return: Feb 20, 2027                                             ║
║  Budget: ₹{ROUND_TRIP_BUDGET_MIN} - ₹{ROUND_TRIP_BUDGET_MAX} (round-trip total)             ║
║  Check interval: Every {CHECK_INTERVAL_MINUTES} minutes                              ║
║  Status: RUNNING                                                   ║
╚════════════════════════════════════════════════════════════════════╝
"""
    )

    check_flights()
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(check_flights)
    print(f"Next check in {CHECK_INTERVAL_MINUTES} minutes.\n")

    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            print("\nTracker stopped.")
            break
        except Exception as error:
            print(f"Unexpected error ({type(error).__name__}); retrying in 60 seconds.")
            time.sleep(60)


if __name__ == "__main__":
    main()
