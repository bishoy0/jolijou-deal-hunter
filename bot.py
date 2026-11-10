
import json
import os
import time
from pathlib import Path
from statistics import median

import requests

STORE = "8ythrr-ay.myshopify.com"
BASE_URL = f"https://{STORE}"

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = Path("data/state.json")

EXTRA_COST_EUR = 60.00
ANOMALY_DROP_PERCENT = 20
MAX_ALERTS_PER_RUN = 10
SALE_MULTIPLIERS = [1.30, 1.50, 2.00]

PLATFORMS = {
    "eBay": {"rate": 0.12, "fixed": 0.35},
    "Vinted": {"rate": 0.00, "fixed": 0.00},
    "Catawiki": {"rate": 0.125, "fixed": 3.00},
    "Shopify": {"rate": 0.025, "fixed": 0.30},
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "JolijouDealHunter/2.0"})


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    response = SESSION.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False
        },
        timeout=25
    )
    response.raise_for_status()


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))

    return {"initialized": False, "products": {}}


def save_state(data):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_exchange_rate(old):
    try:
        response = SESSION.get(
            "https://api.frankfurter.app/latest",
            params={"from": "JPY", "to": "EUR"},
            timeout=20
        )
        response.raise_for_status()

        rate = float(response.json()["rates"]["EUR"])

        if rate <= 0:
            raise ValueError("Taux invalide")

        return rate

    except Exception:
        previous_rate = old.get("last_exchange_rate")

        if previous_rate:
            print("Ancien taux de change utilisé.")
            return float(previous_rate)

        raise Exception("Impossible de récupérer le taux JPY/EUR.")


def euro(value):
    return f"{value:,.2f} €".replace(",", " ").replace(".", ",")


def calculate_cost(price_jpy, rate):
    return price_jpy * rate + EXTRA_COST_EUR


def format_profitability(cost):
    lines = ["📊 SIMULATIONS DE REVENTE"]

    for multiplier in SALE_MULTIPLIERS:
        sale_price = cost * multiplier
        lines.append(
            f"\nPrix de vente simulé : {euro(sale_price)}"
        )

        for name, fees in PLATFORMS.items():
            total_fees = sale_price * fees["rate"] + fees["fixed"]
            profit = sale_price - total_fees - cost

            lines.append(
                f"{name} : bénéfice estimé {euro(profit)}"
            )

    return "\n".join(lines)


def scan_products():
    products = []

    for page in range(1, 41):
        response = SESSION.get(
            f"{BASE_URL}/products.json",
            params={"limit": 250, "page": page},
            timeout=30
        )

        if response.status_code in (401, 403, 404):
            raise Exception(
                f"Catalogue inaccessible : HTTP {response.status_code}"
            )

        response.raise_for_status()
        batch = response.json().get("products", [])

        if not batch:
            break

        products.extend(batch)

        if len(batch) < 250:
            break

        time.sleep(1)

    return products


def main():
    old = load_state()

    # Vérification de Telegram
    send_telegram(
        "🟢 JOLIJOU DEAL HUNTER V2\n"
        "Nouveau scan démarré !"
    )

    rate = get_exchange_rate(old)

    print(f"Taux de change : 1 JPY = {rate} EUR")
    print("Analyse du catalogue...")

    products = scan_products()
    previous = old.get("products", {})
    current = {}

    alerts = 0
    new_count = 0
    drops_count = 0
    anomalies_count = 0

    for product in products:
        prices = []

        for variant in product.get("variants", []):
            try:
                price = float(variant["price"])
                if price > 0:
                    prices.append(price)
            except (KeyError, TypeError, ValueError):
                continue

        if not prices:
            continue

        price_jpy = min(prices)
        product_id = str(product["id"])
        before = previous.get(product_id)

        history = []

        if before:
            history = before.get("history", [])

            if not history:
                old_price = before.get(
                    "price_jpy",
                    before.get("price")
                )
                if old_price is not None:
                    history = [float(old_price)]

        history = [
            float(value) for value in history
            if float(value) > 0
        ]

        item = {
            "title": product.get("title", "Produit sans nom"),
            "price_jpy": price_jpy,
            "url": f"{BASE_URL}/products/{product['handle']}"
        }

        reasons = []

        if before is None and old.get("initialized"):
            reasons.append("🆕 NOUVEAU PRODUIT")
            new_count += 1

        elif history:
            reference_price = history[-1]

            if price_jpy < reference_price:
                reduction = (
                    (reference_price - price_jpy)
                    / reference_price * 100
                )

                reasons.append(
                    f"📉 BAISSE DE PRIX : -{reduction:.1f}%"
                )
                drops_count += 1

                if reduction >= ANOMALY_DROP_PERCENT:
                    reasons.append("🚨 BAISSE IMPORTANTE")
                    anomalies_count += 1

            if len(history) >= 3:
                average = median(history[-10:])

                if price_jpy <= average * (
                    1 - ANOMALY_DROP_PERCENT / 100
                ):
                    if not any(
                        "BAISSE IMPORTANTE" in r for r in reasons
                    ):
                        reasons.append("⚠️ PRIX ANORMALEMENT BAS")
                        anomalies_count += 1

        item["history"] = (history + [price_jpy])[-10:]
        current[product_id] = item

        if reasons and alerts < MAX_ALERTS_PER_RUN:
            cost = calculate_cost(price_jpy, rate)

            message = (
                "🔥 JOLIJOU DEAL HUNTER V2\n\n"
                + "\n".join(reasons)
                + f"\n\n📦 {item['title']}"
                + f"\nPrix fournisseur : {price_jpy:,.0f} ¥"
                + f"\nPrix converti : {euro(price_jpy * rate)}"
                + f"\nSupplément ajouté : {euro(EXTRA_COST_EUR)}"
                + f"\n💰 Coût total estimé : {euro(cost)}"
                + "\n\n"
                + format_profitability(cost)
                + f"\n\n🔗 {item['url']}"
                + "\n\n⚠️ Reventes simulées, pas des prix de marché garantis."
                + "\nFrais indicatifs à vérifier."
            )

            send_telegram(message)
            alerts += 1
            time.sleep(0.5)

    save_state({
        "initialized": True,
        "products": current,
        "last_exchange_rate": rate
    })

    summary = (
        "✅ SCAN JOLIJOU TERMINÉ\n\n"
        f"Produits analysés : {len(current)}\n"
        f"Nouveaux produits : {new_count}\n"
        f"Baisses de prix : {drops_count}\n"
        f"Anomalies : {anomalies_count}\n"
        f"Alertes envoyées : {alerts}\n"
        f"Taux JPY/EUR : {rate}\n"
        f"Supplément par article : {euro(EXTRA_COST_EUR)}"
    )

    print(summary)

    # Envoi systématique du bilan Telegram
    send_telegram(summary)


if __name__ == "__main__":
    main()
