# Hypixel Skyblock Bazaar OFI Analysis

this repo is a small market microstructure project that collects live order-book data from the Skyblock bazaar REST API, and tests whether order flow imbalance has any predictive relationship with short-term price movement.

## What is this?
The bazaar is Skyblock's in-game double-auction market, where players buy and sell large quantities of commodities. Every item has a standing book of buy orders (bids) and sell orders (asks), and the API lists the best prices and volume for each.

This project polls the bazaar API once a minute and logs the top-of-book bid/ask price and volume for 7 items. It then computes OFI between consecutive snapshots, and tests whether the calculated OFI predicts the next-period change in price (using a chronological training/testing split to avoid look-ahead bias). It then reports out of sample correlation and significance for each item.

## Data collection
(bz_data_collector.py)
- Polls [Bazaar API](https://api.hypixel.net/v2/skyblock/bazaar) every 60 seconds.
- Tracked items: BOOSTER_COOKIE, ENCHANTED_DIAMOND, ENCHANTED_DIAMOND_BLOCK, NULL_SPHERE, NULL_OVOID, RECOMBOBULATOR_3000, SUMMONING_EYE.
- pulls the full buy_summary and sell_summary order lists for each item, then takes the best bid (max buy price) and best ask (min sell price), along with the volume at that price level
- Also logs the gap between each successful fetch (Seconds_Since_Fetch) so that the data filtering script can detect any outages or throttling.

The current dataset (bazaar_data.csv) covers about 45 hours of time with 1,914 data samples for each item. So far, this is a single, short collection window, though it may expand in the future as I conduct further analysis on the data I collect.

## Results
The calculated correlation value (OFI vs change in mid-price) varied between -0.109 (for ENCHANTED_DIAMOND_BLOCK) and +0.068 (ENCHANTED_DIAMOND). The highets calculated p-value was 0.982 (NULL_OVOID), and the lowest was 0.0092 (ENCHANTED_DIAMOND_BLOCK).

However, with 7 items tested simultaneously, Bonferroni-corrected significance threshold is p < 0.0071. No item's out-of-sample correlation passes that threshold.

Conclusively, at the current sample size, the analysis does not find any convincing evidence of a predictive OFI-price change relationship for the selected items.

## Running 

```
python bz_data_collector.py        # run continuously to collect data (Ctrl+C to stop)
python bz_ofi_pipeline.py      # process collected data and generate results
```
# My Notes
- this is me talking myself through my process and code.

explanation for null result on skyblock project:
basically the test i ran was to see if the short-term changes in volume could accurately predict mid-price movements (mid price being average of top bids/ask).

Essentially what i learned the hard way was that in high frequency trading, like in the skyblock market, volume signals disappear incredibly fast, much faster than i could update the dataset. by the time a minute passed, the signal was mostly random noise. to pick up on any real relationship, i would need a much faster tracking setup to capture data every millisecond rather than every minute. the model I made returned a null result because metrics like OFI decay far much faster than I measured and need millisecond-level tick data to return results that aren't just noise.

Basically: the short term volume changes i measured don't predict price movements because metrics like OFI decay quickly, and my measurements were too slow.

## intended next steps
- several more weeks of data
- immutable raw data plus cleaned Parquet files and a documented schema
- retry/backoff, rate-limit handling, structured logging, and monitoring
- unit and integration tests with github actions
- a cli interface & reproducible environment
- walk-forward validation, multiple prediction horizons, naive baselines, bootstrap confidence intervals, effect sizes, and a power analysis
- a cost-aware or spread-aware backtest, regardless of conclusion
- polished 4-6 page paper or technical report with charts and limitations