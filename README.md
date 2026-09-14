# Hypixel Skyblock Bazaar OFI Analysis

this repo is a small market microstructure project that collects live order-book data from the Hypixel Skyblock bazaar REST API, testing whether order flow imbalance has any predictive relationship with short-term price movement.

The current corrected results do not find any convincing evidence of such a relationship, but the dataset I'm using for analysis is still growing, and the current significance calculation doesn't yet account for time-series dependence.


## What is this?
The bazaar is Skyblock's in-game double-auction market, where players buy and sell large quantities of commodities. Every item has a standing book of buy orders (bids) and sell orders (asks), and the API lists the best prices and volume for each.

This project polls the bazaar API once a minute and logs the top-of-book bid/ask price and volume for 7 items. It then computes OFI between consecutive snapshots, and tests whether the calculated OFI predicts the next-period change in price (using a chronological training/testing split to avoid look-ahead bias). It then reports out of sample correlation and significance for each item.


## Data collection
(bz_data_collector.py)
- Polls [Bazaar API](https://api.hypixel.net/v2/skyblock/bazaar) every 60 seconds.
- Tracked items: BOOSTER_COOKIE, ENCHANTED_DIAMOND, ENCHANTED_DIAMOND_BLOCK, NULL_SPHERE, NULL_OVOID, RECOMBOBULATOR_3000, SUMMONING_EYE.
- pulls the full buy_summary and sell_summary order lists for each item, then takes the best bid (max buy price) and best ask (min sell price), along with the volume at that price level
- Also logs the gap between each successful fetch (Seconds_Since_Fetch) so that the data filtering script can detect any outages or throttling.



Data and quality checks (9/13/2026)

The frozen input contained ~40,000 rows covering seven Bazaar items from September 9 through September 13, 2026. All frozen rows passed the script's basic checks, so no rows were rejected for invalid timestamps, missing values, duplicate item timestamp pairs, non-positive prices, negative volumes, or crossed quotes.

The script divided the data into 186 continuous per-item segments. A new segment was started after a collection gap longer than 90 seconds, which prevented OFI or future-return calculations from crossing an outage.

The training/test cutoff was September 12, 2026 at approximately 17:30 UTC. The last training target ended before the first test feature, so the results do not have direct leakage across that boundary.

The final test set contained 11,558 predictions. The accepted prediction horizons averaged about 60.4 seconds and ranged from about 60.2 to 67.6 seconds, so the target was consistently close to one minute.


## Results

Version 0.3 tested whether snapshot order flow imbalance could predict the Bazaar midpoint return approximately one minute later. The analysis used about 40,000 valid observations across seven items, separated collection outages into continuous segments, trained each item's model on the earlier 70% of the data, and evaluated it on the later 30%. All seven OFI/return correlations were close to zero, ranging from -0.047 to +0.034. The OFI model also produced a higher mean absolute error than both a zero-return baseline and a training-average baseline for every item. 

These results do not show a useful one-minute predictive relationship in the current sample, although they do not rule out other OFI definitions, horizons, market conditions, or nonlinear relationships. Because this is one short dataset with a single holdout period and no time-series-aware confidence intervals, the result should be treated as a preliminary null finding.

In the predictions-versus-outcomes chart, a perfect model would place its points near the diagonal dashed line. Instead, the predictions form nearly horizontal bands around zero while the actual returns move over much wider ranges. This means the model usually predicted almost the same small return regardless of what happened next.



## Notable limitations

- Short sample: the relatively small sample size is not representative of different days, player population/counts, updates, or game events.
- Many zero returns: two items have unchanged mid-prices between two snapshots more than 70% of the time.
- One horizon: the target is the next succesful snapshot, normally about 60 seconds later, rather than a set of fixed elapsed-time horizons.
- No fitted baseline: at the moment, the current training data block is unused, and the analysis does not compare predictions with zero-change, sign, or persistence baselines.
- Top-of-book only: displayed price and quantity at the best level don't describe the full order book or executed trades.

## Running 

```
python bz_data_collector.py        # run continuously to collect data (Ctrl+C to stop)
python bz_ofi_pipeline.py      # process collected data and generate results
```


## intended next steps
- several more weeks of data
- immutable raw data plus cleaned Parquet files and a documented schema
- retry/backoff, rate-limit handling, structured logging, and monitoring
- unit and integration tests with github actions
- a cli interface & reproducible environment
- walk-forward validation, multiple prediction horizons, naive baselines, bootstrap confidence intervals, effect sizes, and a power analysis
- a cost-aware or spread-aware backtest, regardless of conclusion