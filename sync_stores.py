#!/usr/bin/env python3
import json
import logging
import requests
import os
import sys
import tempfile
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("./logs.txt"),
        logging.StreamHandler(),
    ],
)

logging.info("==============================")
logging.info("Starting test script...")
logging.info("==============================")


class WooStock:
    name: str
    sku: str
    stock_quantity: int
    product_id: int
    variation_id: int

    def __init__(self, name, sku, stock_quantity, product_id, variation_id):
        self.name = name
        self.product_id = product_id
        self.variation_id = variation_id
        self.sku = sku
        if stock_quantity is None:
            stock_quantity = 0
        if stock_quantity < 0:
            stock_quantity = 0
        self.stock_quantity = stock_quantity

    def __repr__(self):
        return f"{self.name}, {self.sku}: {self.stock_quantity}"
    
class EtsyStock:
    sku: str
    stock_quantity: int
    listing_id: int
    product_id: int

    def __init__(self, sku, stock_quantity, listing_id, product_id):
        self.listing_id = listing_id
        self.product_id = product_id
        self.sku = sku
        if stock_quantity is None:
            stock_quantity = 0
        if stock_quantity < 0:
            stock_quantity = 0
        self.stock_quantity = stock_quantity

    def __repr__(self):
        return f"{self.sku}: {self.stock_quantity}"


class StockItem:
    sku: str
    name: str
    woo_stock: WooStock
    etsy_stock: EtsyStock

    def __init__(self, sku, name, woo_stock, etsy_stock):
        self.sku = sku
        self.name = name
        self.woo_stock = woo_stock
        self.etsy_stock = etsy_stock

    def __repr__(self):
        return f"<< ETSY: {self.etsy_stock.stock_quantity} | WOO: {self.woo_stock.stock_quantity} >>"


def save_config(config, config_file):
    """Save configuration back to config.json file"""
    if config_file and os.path.exists(config_file):
        try:
            with open(config_file, "w") as f:
                json.dump(config, f, indent=2)
            logging.info("Saved updated configuration to config.json")
            return True
        except Exception as e:
            logging.error(f"Error saving config.json: {e}")
            return False
    return False


def load_config():
    """Load configuration from environment variables or config.json file"""
    # Try to load from config file first
    config_file = os.path.join(os.path.dirname(__file__), "config.json")

    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                config = json.load(f)
                logging.info("Loaded configuration from config.json")
                return config, config_file
        except Exception as e:
            logging.warning(f"Error reading config.json: {e}")

    return config, None


# Load configuration
CONFIG, CONFIG_FILE = load_config()

# Extract config values
WC_URL = CONFIG.get("woocommerce", {}).get("url", "")
WC_CONSUMER_KEY = CONFIG.get("woocommerce", {}).get("consumer_key", "")
WC_CONSUMER_SECRET = CONFIG.get("woocommerce", {}).get("consumer_secret", "")

ETSY_KEYSTRING = CONFIG.get("etsy", {}).get("api_key", "")
ETSY_SHARED_SECRET = CONFIG.get("etsy", {}).get("shared_secret", "")
ETSY_API_KEY = (
    f"{ETSY_KEYSTRING}:{ETSY_SHARED_SECRET}" if ETSY_SHARED_SECRET else ETSY_KEYSTRING
)
ETSY_ACCESS_TOKEN = CONFIG.get("etsy", {}).get("access_token", "")
ETSY_REFRESH_TOKEN = CONFIG.get("etsy", {}).get("refresh_token", "")
ETSY_SHOP_ID = CONFIG.get("etsy", {}).get("shop_id", "")

WRITE = CONFIG.get("write", False)


# Validate configuration
if not all([WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, ETSY_API_KEY, ETSY_SHOP_ID]):
    logging.error(
        "Missing required configuration! Please check config.json or environment variables."
    )
    logging.error(
        "Required: WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, ETSY_API_KEY, ETSY_SHOP_ID"
    )
    sys.exit(1)

################
### Etsy
################
def refresh_etsy_token():
    """Refresh the Etsy access token using the refresh token"""
    global ETSY_ACCESS_TOKEN, ETSY_REFRESH_TOKEN, CONFIG

    if not ETSY_REFRESH_TOKEN:
        logging.error("No refresh token available. Cannot refresh access token.")
        return False

    try:
        url = "https://api.etsy.com/v3/public/oauth/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": ETSY_KEYSTRING,
            "refresh_token": ETSY_REFRESH_TOKEN,
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "FlotsamMade-WooCommerce-Etsy-Sync/1.0",
        }

        logging.info("Refreshing Etsy access token...")
        response = requests.post(url, data=data, headers=headers, timeout=30)
        response.raise_for_status()

        token_response = response.json()

        # Update global variables
        ETSY_ACCESS_TOKEN = token_response["access_token"]

        # Update refresh token if a new one was provided
        if "refresh_token" in token_response:
            ETSY_REFRESH_TOKEN = token_response["refresh_token"]

        # Save to config file
        CONFIG["etsy"]["access_token"] = ETSY_ACCESS_TOKEN
        if "refresh_token" in token_response:
            CONFIG["etsy"]["refresh_token"] = ETSY_REFRESH_TOKEN

        if save_config(CONFIG, CONFIG_FILE):
            logging.info("Successfully refreshed and saved new Etsy access token")
        else:
            logging.warning("Token refreshed but could not save to config file")

        return True

    except requests.exceptions.RequestException as e:
        logging.error(f"Error refreshing Etsy token: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logging.error(f"Response: {e.response.text}")
        return False


def get_all_etsy_listings(active=True, retry_on_auth_error=True):
    """Fetch all listings from Etsy shop"""
    logging.info(f"Fetching {'active' if active else 'sold_out'} listings from Etsy...")
    url = f"https://api.etsy.com/v3/application/shops/{ETSY_SHOP_ID}/listings"
    headers = {
        "x-api-key": ETSY_API_KEY,
        "Authorization": f"Bearer {ETSY_ACCESS_TOKEN}",
        "User-Agent": "FlotsamMade-WooCommerce-Etsy-Sync/1.0",
    }
    response = requests.get(
        url,
        headers=headers,
        timeout=30,
        params={"limit": 100, "state": "active" if active else "sold_out"},
    )

    # Check for auth errors (401 Unauthorized)
    if response.status_code == 401 and retry_on_auth_error:
        logging.warning("Etsy token appears to be expired. Attempting to refresh...")
        if refresh_etsy_token():
            # Retry with new token
            return get_all_etsy_listings(active=active, retry_on_auth_error=False)
        else:
            logging.error("Failed to refresh token")
            return None

    response.raise_for_status()
    return response.json()


def get_etsy_variants_stock(listing_id: int, retry_on_auth_error=True):
    url = (
        f"https://api.etsy.com/v3/application/listings/{listing_id}?includes=inventory"
    )
    logging.info(f"Fetching Etsy variants stock for listing ID: {listing_id}")
    headers = {
        "x-api-key": ETSY_API_KEY,
        "Authorization": f"Bearer {ETSY_ACCESS_TOKEN}",
        "User-Agent": "FlotsamMade-WooCommerce-Etsy-Sync/1.0",
    }
    response = requests.get(url, headers=headers, timeout=30)

    # Check for auth errors (401 Unauthorized)
    if response.status_code == 401 and retry_on_auth_error:
        logging.warning("Etsy token appears to be expired. Attempting to refresh...")
        if refresh_etsy_token():
            # Retry with new token
            return get_etsy_variants_stock(listing_id, retry_on_auth_error=False)
        else:
            logging.error("Failed to refresh token")
            return None

    response.raise_for_status()

    ## Returns the whole response, we may want to extract just the stock
    return response.json()


################
### Woo Commerce
################
def get_variation_to_stock_map(product_id: int, product_name: str) -> dict[str, WooStock]:
    logging.info(f"Fetching Woo variations for product ID: {product_id}")
    var_url = f"{WC_URL}/wp-json/wc/v3/products/{product_id}/variations"
    var_response = requests.get(
        var_url, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), timeout=30
    )
    var_response.raise_for_status()
    variations = var_response.json()
    return {
        var["sku"]: WooStock(
            name=product_name + " --- " + var.get("name", ""),
            sku=var.get("sku"),
            stock_quantity=var.get("stock_quantity", 0) or 0,
            product_id=product_id,
            variation_id=var.get("id"),
        )
        for var in variations
    }


def get_woocommerce_stock():
    logging.info("Fetching products from WooCommerce...")
    try:
        url = f"{WC_URL}/wp-json/wc/v3/products"
        params = {"per_page": 100, "status": "publish"}  # Adjust as needed

        response = requests.get(
            url, params=params, auth=(WC_CONSUMER_KEY, WC_CONSUMER_SECRET), timeout=30
        )
        response.raise_for_status()

        products = response.json()
        logging.info(f"Fetched {len(products)} products from WooCommerce")

        sku_to_stock = {}
        for product in products:
            sku_to_stock.update(get_variation_to_stock_map(product["id"], product["name"]))

        return sku_to_stock

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching WooCommerce products: {e}")
        return {}
    

def get_stock_items() -> dict[str, StockItem]:
    sku_to_woo_stock = get_woocommerce_stock()

    ### ETSY DOCS - Reference again for writes!
    ### https://developers.etsy.com/documentation/reference/#operation/getListingsByShop

    active_listings = get_all_etsy_listings()
    sold_out_listings = get_all_etsy_listings(active=False)
    all_etsy_listings = active_listings["results"] + sold_out_listings["results"]

    has_woo_skus = [
        listing
        for listing in all_etsy_listings
        if any([sku in sku_to_woo_stock.keys() for sku in listing["skus"]])
    ]

    sku_to_stock_items: dict[str, StockItem] = {}
    bad_skus = set()

    for listing in has_woo_skus:
        logging.info(f"{listing['listing_id']}: {listing['title']}")
        res = get_etsy_variants_stock(listing["listing_id"])
        for sub_product in res["inventory"]["products"]:
            if sub_product["sku"] in sku_to_stock_items:
                continue
            if sub_product["sku"] in bad_skus:
                continue
            if sub_product["sku"] not in sku_to_woo_stock:
                logging.warning(
                    f"SKU {sub_product['sku']} from Etsy listing {listing['listing_id']} not found in WooCommerce stock data"
                )
                bad_skus.add(sub_product["sku"])
                continue
            etsy_stock_item = EtsyStock(
                sku=sub_product["sku"],
                stock_quantity=sub_product["offerings"][0]["quantity"],
                listing_id=listing["listing_id"],
                product_id=sub_product["product_id"],
            )
            sku_to_stock_items[sub_product["sku"]] = StockItem(
                sku=sub_product["sku"],
                name=sku_to_woo_stock[sub_product["sku"]].name,
                woo_stock=sku_to_woo_stock[sub_product["sku"]],
                etsy_stock=etsy_stock_item,
            )
    
    return sku_to_stock_items

def get_db():
    # Load in dictionary from db.json
    # If db.json doesn't exist, create it with an empty dictionary
    if not os.path.exists("db.json"):
        logging.info("db.json not found, creating new one with empty dictionary")
        with open("db.json", "w") as f:
            json.dump({}, f)
        return {}
    
    # If db.json exists but is empty, initialize it with an empty dictionary
    if os.path.getsize("db.json") == 0:
        logging.info("db.json is empty, initializing with empty dictionary")
        with open("db.json", "w") as f:
            json.dump({}, f)
        return {}

    logging.info("Loading stock data from db.json")
    with open("db.json", "r") as f:
        return json.load(f).get("items", {}) or {}
    
def write_backup_db(data):
    # Write a backup of the db to backups/db_backup_MM_DD_YYYY.json if one does not exist
    date_string = datetime.now().strftime("%m_%d_%Y")
    hour = datetime.now().hour
    backup_dir = f"backups/{date_string}"
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    # If a backup for the current hour already exists, return
    
    backup_path = os.path.join(backup_dir, f"db_backup_{date_string}_{hour}.json")
    if os.path.exists(backup_path):
        return
    with open(backup_path, "w") as f:
        json.dump(data, f, indent=4)

def set_db(data):
    if not WRITE:
        logging.info("WRITE flag is False. Skipping writing to db.json")
        return
    logging.info("Saving updated stock data to db.json")
    timestamp = datetime.now().isoformat()
    data = {"last_updated": timestamp, "items": data}
    fd, tmp_path = tempfile.mkstemp(dir=".", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp_path, "db.json")  # atomic on POSIX
    write_backup_db(data)

def cleanup_old_backups(days_to_keep=21):
    cutoff_date = datetime.now() - timedelta(days=days_to_keep)
    for dirname in os.listdir("backups"):
        dirpath = os.path.join("backups", dirname)
        if os.path.isdir(dirpath):
            try:
                backup_date = datetime.strptime(dirname, "%m_%d_%Y")
                if backup_date < cutoff_date:
                    for filename in os.listdir(dirpath):
                        file_path = os.path.join(dirpath, filename)
                        os.remove(file_path)
                    os.rmdir(dirpath)
                    logging.info(f"Deleted old backup directory: {dirpath}")
            except ValueError:
                logging.warning(f"Unexpected backup directory format: {dirname}")

class APIException(Exception):
    pass

def update_etsy(new_stock):
    if not WRITE:
        logging.info(f"WRITE flag is False. Skipping Etsy update to {new_stock}")
        return "skipped"
    ### TODO! THIS NEEDS TO BE FILLED OUT! 
    ### MAKE SURE TO ONLY MAKE UPDATES TO TEST ITEMS AT FIRST!
    ### https://www.etsy.com/legal/policy/api-testing-policy/169130941112
    logging.info(f"Updating Etsy stock to {new_stock}")
    # status = "failure"
    status = "success"
    if status == "failure":
        raise APIException("Failed to update Etsy stock")
    return status

def update_woo(new_stock):
    if not WRITE:
        logging.info(f"WRITE flag is False. Skipping WooCommerce update to {new_stock}")
        return "skipped"
    ### TODO! THIS NEEDS TO BE FILLED OUT
    logging.info(f"Updating WooCommerce stock to {new_stock}")
    # status = "failure"
    status = "success"
    if status == "failure":
        raise APIException("Failed to update WooCommerce stock")
    return status

def make_updates(sku_to_stock_items: dict[str, StockItem]) -> None:
    fake_ass_database = get_db()
    try:
        for sku, stock_item in sku_to_stock_items.items():
            logging.info("==============================")
            logging.info(f"Processing SKU: {sku}, Name: {stock_item.name}, Woo IDs: {stock_item.woo_stock.product_id}/{stock_item.woo_stock.variation_id}, Etsy IDs: {stock_item.etsy_stock.listing_id}/{stock_item.etsy_stock.product_id}")
            try:
                woo_stock_quantity = stock_item.woo_stock.stock_quantity
                etsy_stock_quantity = stock_item.etsy_stock.stock_quantity
                if sku not in fake_ass_database:
                    logging.info(f"SKU {sku} not in database")
                    if woo_stock_quantity != etsy_stock_quantity:
                        logging.info("SKU not in database and Woo is discrepant from Etsy: Updating Woo")
                        update_woo(etsy_stock_quantity)
                    fake_ass_database[sku] = etsy_stock_quantity
                    continue

                etsy_diff = etsy_stock_quantity - fake_ass_database[sku]
                woo_diff = woo_stock_quantity - fake_ass_database[sku]
                logging.info(f"Database stock = {fake_ass_database[sku]} | Etsy stock = {etsy_stock_quantity} | Woo stock = {woo_stock_quantity}")
                logging.info(f"Etsy diff = {etsy_diff} | Woo diff = {woo_diff}")

                if etsy_diff == 0 and woo_diff == 0:
                    logging.info(f"No differences found for SKU: {sku}")
                    continue

                if etsy_diff > 0:
                    logging.info(f"Positive Etsy diff = {etsy_diff}. Updating Woo.")
                    if woo_diff != 0:
                        logging.warning(f"Etsy positive diff coexisting with woo diff, SKU: {sku}")
                    update_woo(etsy_stock_quantity)
                    fake_ass_database[sku] = etsy_stock_quantity
                    continue

                if woo_diff > 0:
                    logging.info(f"Positive Woo diff = {woo_diff}. Updating Etsy.")
                    if etsy_diff != 0:
                        logging.warning(f"Woo positive diff coexisting with etsy diff, SKU: {sku}")
                    update_etsy(woo_stock_quantity)
                    fake_ass_database[sku] = woo_stock_quantity
                    continue

                total_diff = etsy_diff + woo_diff
                new_stock = fake_ass_database[sku] + total_diff
                if new_stock < 0:
                    logging.error(f"New stock for SKU: {sku} is negative. Alerting to Amy.")
                    new_stock = 0

                if etsy_diff != total_diff:
                    logging.info(f"Updating Etsy.")
                    update_etsy(new_stock)
                if woo_diff != total_diff:
                    logging.info(f"Updating Woo.")
                    update_woo(new_stock)
                fake_ass_database[sku] = new_stock
            except APIException as e:
                logging.error(f"APIException occurred for SKU: {sku}. Skipping db writes. Error: {e}")
                continue
    except Exception as e:
        logging.exception(f"An error occurred: {e}")

    set_db(fake_ass_database)

if __name__ == "__main__":
    sku_to_stock_items = get_stock_items()
    make_updates(sku_to_stock_items)


# Cron
# 	/home/flotanzo/sync_script/test.py >> test_errors.txt 2>&1