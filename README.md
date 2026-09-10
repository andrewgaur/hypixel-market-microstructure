# Hypixel Skyblock Bazaar OFI Analysis

this repo is a small market microstructure project that collects live order-book data from the Hypixel Skyblock bazaar REST API, testing whether order flow imbalance has any predictive relationship with short-term price movement.

The current corrected results do not find any convincing evidence of such a relationship, but the dataset I'm using for analysis is still growing. At the moment it only covers about 34 hours with a few collection interruptions, and the current significance calculation doesn't yet account for time-series dependence.


## What is this?
The bazaar is Skyblock's in-game double-auction market, where players buy and sell large quantities of commodities. Every item has a standing book of buy orders (bids) and sell orders (asks), and the API lists the best prices and volume for each.

This project polls the bazaar API once a minute and logs the top-of-book bid/ask price and volume for 7 items. It then computes OFI between consecutive snapshots, and tests whether the calculated OFI predicts the next-period change in price (using a chronological training/testing split to avoid look-ahead bias). It then reports out of sample correlation and significance for each item.


## Data collection
(bz_data_collector.py)
- Polls [Bazaar API](https://api.hypixel.net/v2/skyblock/bazaar) every 60 seconds.
- Tracked items: BOOSTER_COOKIE, ENCHANTED_DIAMOND, ENCHANTED_DIAMOND_BLOCK, NULL_SPHERE, NULL_OVOID, RECOMBOBULATOR_3000, SUMMONING_EYE.
- pulls the full buy_summary and sell_summary order lists for each item, then takes the best bid (max buy price) and best ask (min sell price), along with the volume at that price level
- Also logs the gap between each successful fetch (Seconds_Since_Fetch) so that the data filtering script can detect any outages or throttling.

The current dataset ```bazaar_quotes_utc.csv``` contains about 12,000 rows: ~1,700 complete snapshots for each of the seven tracked items. The current version of the dataset covers about 34 hours of data collection.

Basic integrity checks passed:
- all timestamps contain all 7 tracked items
- no duplicate `(Item, Timestamp)` rows were found
- no cross quotes (`Top_Bid > Top_Ask`) were found
- the matching raw archive contains the same number of compressed API responses
- the median sample interval is about 60 seconds
- analysis excludes any observations immediately adjacent to gaps longer than the analysis threshold of 90 seconds


## Results
The last 30% of usable observations for each item forms the current test set. It contains ~500 observations for each item and covers about 10 hours of data collection.

All the estimated correlations are close to zero. None pass even an uncorrected `p < 0.05` threshold, and none pass the adjusted Bonferroni threshold of `p < 0.0071`. The least liquid-looking target items also have many unchanged 60-second outcomes: about 75% of `NULL_OVOID` test returns and 70% of `NULL_SPHERE` test returns are zero. Pearson correlation is therefore an especially rough summary for these items.

For this specific corrected sample and single forward-looking target, the analysis found no significant linear OFI/price-change association. The largest absolute estimated correlation is only `0.0646`.


## Notable limitations

- Short sample: roughly 34 hours is not representative of different days, player population/counts, updates, or game events.
- Many zero returns: two items have unchanged mid-prices between two snapshots more than 70% of the time.
- One horizon: the target is the next succesful snapshot, normally about 60 seconds later, rather than a set of fixed elapsed-time horizons.
- No fitted baseline: at the moment, the current training data block is unused, and the analysis does not compare predictions with zero-change, sign, or persistence baselines.
- Top-of-book only: displayed price and quantity at the best level don't describe the full order book or executed trades.

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