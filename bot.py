
import json
import os
import time
from pathlib import Path

import requests

STORE = "8ythrr-ay.myshopify.com"
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = Path("data/state.json")
BASE_URL = f"https://{STORE}"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20
    )
    response.raise_for_status()

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"initialized": False, "products": {}}

def scan_products():
    products = []

    for page in range(1, 41):
        url = f"{BASE_URL}/products.json?limit=250&page={page}"
        response = requests.get(url, timeout=30)

        if response.status_code in (401, 403, 404):
            raise Exception(
                f"Catalogue inaccessible : {response.status_code}"
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
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    old = load_state()
    products = scan_products()

    current = {}

    for p in products:
        variants = p.get("variants", [])
        prices = [
            float(v["price"])
            for v in variants
            if v.get("price") is not None
        ]

        current[str(p["id"])] = {
            "title": p["title"],
            "price": min(prices) if prices else None,
            "url": f"{BASE_URL}/products/{p['handle']}"
        }

    if not old["initialized"]:
        STATE_FILE.write_text(json.dumps({
            "initialized": True,
            "products": current
        }, indent=2))

        send_telegram(
            f"🟢 Jolijou Deal Hunter connecté !\n"
            f"{len(current)} produits enregistrés."
        )
        return

    previous = old["products"]
    alerts = 0

    for pid, item in current.items():
        before = previous.get(pid)

        if before is None:
            message = (
                f"🆕 NOUVEAU PRODUIT\n\n"
                f"{item['title']}\n"
                f"Prix : {item['price']}\n"
                f"{item['url']}"
            )
        elif (
            item["price"] is not None
            and before["price"] is not None
            and item["price"] < before["price"]
        ):
            message = (
                f"📉 BAISSE DE PRIX\n\n"
                f"{item['title']}\n"
                f"Ancien prix : {before['price']}\n"
                f"Nouveau prix : {item['price']}\n"
                f"{item['url']}"
            )
        else:
            continue

        send_telegram(message)
        alerts += 1

        time.sleep(0.5)

    STATE_FILE.write_text(json.dumps({
        "initialized": True,
        "products": current
    }, indent=2))

    print(f"Scan terminé : {alerts} alertes.")

if __name__ == "__main__":
    main()
