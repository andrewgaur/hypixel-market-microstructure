#hypixel skyblock bazaar ofi
import requests
import time
import os
import csv
from datetime import datetime

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
CSV_FILENAME = "bazaar_data.csv"

#create csv file w/ headers
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
        #JSON text to python dictionary
        data=response.json()

        #check if API returned properly
        if data.get("success") == True:

            #checking time
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            now = time.time()

            if last_fetch_time is None:
                gap_seconds = None
                #1st successful fetch
            else:
                gap_seconds = now - last_fetch_time
                if gap_seconds > GAP_THRESHOLD:
                    print(f"[{current_time}] warning: {gap_seconds:.0f}s since last fetch, expected ~{EXPECTED_INTERVAL}s")

            last_fetch_time = now

            '''
            #find specific target item
            product_data = data["products"][TARGET_ITEMS]["quick_status"]
            #extract needed numbers
            top_bid = product_data["sellPrice"]
            top_ask = product_data["buyPrice"]
            bid_volume = product_data["buyVolume"]
            ask_volume = product_data["sellVolume"]
            '''
            
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

                        #product_data = data["products"][item]["quick_status"]
                        '''
                        #this is lowk the wrong data to calculate OFI
                        top_bid = product_data["sellPrice"]
                        top_ask = product_data["buyPrice"]
                        bid_volume = product_data["buyVolume"]
                        ask_volume = product_data["sellVolume"]
                        '''
                        #bid side
                        buy_orders =  data["products"][item]["buy_summary"]
                        #ask side
                        sell_orders = data["products"][item]["sell_summary"]


                        #explicitly take price and amount
                        best_bid = max(buy_orders, key=lambda o: o["pricePerUnit"], default=None)
                        best_ask = min(sell_orders, key=lambda o: o["pricePerUnit"], default=None)

                        best_bid_price, best_bid_qty = (best_bid["pricePerUnit"], best_bid["amount"]) if best_bid else (None, None)
                        best_ask_price, best_ask_qty = (best_ask["pricePerUnit"], best_ask["amount"]) if best_ask else (None, None)

                        '''
                        alternative price and amount logic
                        best_bid_price = max((o["pricePerUnit"] for o in buy_orders), default=None)
                        best_bid_qty = next((o["amount"] for o in buy_orders if o["pricePerUnit"] == best_bid_price), None)

                        best_ask_price = min((o["pricePerUnit"] for o in sell_orders), default=None)
                        best_ask_qty = next((o["amount"] for o in sell_orders if o["pricePerUnit"] == best_ask_price), None)
                        '''


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
    
