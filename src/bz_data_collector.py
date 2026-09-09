#hypixel skyblock bazaar ofi
import requests
import time
import os
import csv
from datetime import datetime, timezone
import gzip
from uuid import uuid4
from pathlib import Path

#timezone: need to record UTC
#gzip: use to compress saved responses to reduce disk usage
#uuid4 generate unique filenames


# bazaar api url
URL = "https://api.hypixel.net/v2/skyblock/bazaar"
#item
TARGET_ITEMS = ["BOOSTER_COOKIE",
                "ENCHANTED_DIAMOND",
                "ENCHANTED_DIAMOND_BLOCK",
                "NULL_SPHERE",
                "NULL_OVOID",
                "RECOMBOBULATOR_3000",
                "SUMMONING_EYE"
                ]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# need folder for original raw responses from API
# my csv will contain convenient extracted values. raw folder preserves
# response bodies before the script selects/filters anything
RAW_DIR = DATA_DIR / "raw"
RAW_DIR.mkdir(exist_ok = True)


CSV_FILENAME = DATA_DIR / "bazaar_quotes_utc.csv"

#create csv /efile w/ headers
# mode = write to make new file

#check if file exists
if not os.path.exists(CSV_FILENAME):
    #doesn't exist, create file
    with open(CSV_FILENAME, mode = 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Timestamp',
                         'Item',
                         'Top_Bid',
                         'Top_Ask',
                         'Bid_Volume',
                         'Ask_Volume',
                         'Seconds_Since_Fetch'
                         ])
    print("created new csv file")
else:
    #does exist, do nothing
    print("existing csv file found")


#inform started
print("tracking started, keyboard interrupt to stop")

#tracking time between fetches

last_fetch_time = None
#sleep time of 60 sec
EXPECTED_INTERVAL = 60
#warning threshold for interval higher than expected
GAP_THRESHOLD = 90


#3 continuous loop of importing data

while True:
    try:
        response = requests.get(URL, timeout=15)

        # record when computer finishes receiving the response from the API
        received_at = datetime.now(timezone.utc)
        current_time = received_at.isoformat(timespec="microseconds")

        # put each day's responses in a separate folder
        day_dir = RAW_DIR / received_at.strftime("%Y-%m-%d")
        day_dir.mkdir(exist_ok = True)

        #windows safe timestamp and a unique id
        filename = (
            received_at.strftime("%Y%m%dT%H%M%S_%fZ")
            + "_"
            + uuid4().hex
            + ".body.gz"
        )
        raw_path = day_dir / filename


        #save the original response body before filtering
        # "xb" creates new file and refuses to overwrite if one already exists
        with raw_path.open("xb") as raw_file:
            with gzip.GzipFile(fileobj=raw_file, mode="wb") as compressed:
                compressed.write(response.content)

        # detect failed HTTP responses after preserving bodies
        response.raise_for_status()

        #convert the JSON response into a python dict
        data = response.json()

        '''
        notes to self:
        The .body.gz extension means a compressed response body. Successful responses
        contain JSON, but an error response might contain something else. Saving first
        preserves either one

        If a request fails before any response arrives, there is nothing to save, so
        the existing exception handler will print the error
        '''

        #check if API returned properly
        if data.get("success") == True:

            #checking time
            now = time.time()

            if last_fetch_time is None:
                gap_seconds = None
                #1st successful fetch
            else:
                gap_seconds = now - last_fetch_time
                if gap_seconds > GAP_THRESHOLD:
                    print(f"[{current_time}] warning: {gap_seconds:.0f}s since last fetch, expected ~{EXPECTED_INTERVAL}s")

            last_fetch_time = now

            
            #save data to csv
            #mode a for append to add new row
            with open(CSV_FILENAME, mode='a', newline='') as file:
                writer = csv.writer(file)

                #loop through each item in targeted list
                for item in TARGET_ITEMS:

                    #check item is in data
                    if item not in data["products"]:
                        print(f"[{current_time}] warning, '{item}' not in API response (check ID?)")
                        continue

                    if item in data["products"]:

                        product = data["products"][item]

                        '''
                        skyblock names the summaries backwards,
                        from the perspective of someone trading immediately
                        ie, the players perspective
                        buy summary is the offers I can buy from (asks)
                        sell summary is the offers I can sell into (bids)
                        '''
                        #standing buyers, prices you recieve when selling instantly
                        bid_orders =  product["sell_summary"]

                        #standing sellers, prices you pay when buying instantly
                        ask_orders = product["buy_summary"]


                        #explicitly take price and amount
                        best_bid = max(
                            bid_orders, 
                            key=lambda o: o["pricePerUnit"], 
                            default=None
                            )
                        
                        best_ask = min(
                            ask_orders, 
                            key=lambda o: o["pricePerUnit"], 
                            default=None
                            )

                        best_bid_price, best_bid_qty = (best_bid["pricePerUnit"], best_bid["amount"]) if best_bid else (None, None)
                        best_ask_price, best_ask_qty = (best_ask["pricePerUnit"], best_ask["amount"]) if best_ask else (None, None)

                        #have to double check the backwards labelling isnt crossing the data
                        if best_bid_price is None or best_ask_price is None:
                            print(f"Skipping {item}: missing bid or ask")
                            continue
                        if best_bid_price > best_ask_price:
                            print(f"Skipping {item}: crossed quotes")
                            continue
                        

                        #save specific item's row
                        writer.writerow([
                            current_time,
                            item,
                            best_bid_price,
                            best_ask_price,
                            best_bid_qty,
                            best_ask_qty,
                            gap_seconds
                        ])

            #check working
            print(f"[{current_time}] data logged properly")

    except Exception as e:
        #dont break if something stops ie internet, api crash, etc
        print(f"error: {e}")

    #sleep 60 seconds before recording data again
    time.sleep(60)
    
