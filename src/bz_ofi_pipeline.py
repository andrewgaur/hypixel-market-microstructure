'''
a small, standalone analysis of hypixel bazaar snapshot OFI

this version answers a single question:
does snapshot ofi help predict the midpoint return approximately one minute later?

at the moment, the script:

freezes and checks the collector CSV,
prevents calculations from crossing invalid rows or collection outages,
calculates OFI for the given snapshot,
defines a future midpoint return over a single elapsed-time horizon,
trains one simple line per item on the earlier 70% of the time,
evaluates that frozen line on the later 30%,
and compares it with predicting zero or the training-period average

There are later additions I intend to implement, such as multiple horizons or
trading backtests, but only after this smaller version is fully working and understood

'''


from __future__ import annotations
# so type hints can refer to objects without having to evaluate very hint
# while the file starts

# standard library tools
import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
import matplotlib.pyplot as plt

# the two required analysis libraries
import numpy as np
import pandas as pd


from scipy import stats
import os


# the CSV from the collector must contain these fields. 
# helps with finding errors here instead of in failed calculations later
REQUIRED_COLUMNS = (
    "Timestamp",
    "Item",
    "Top_Bid",
    "Top_Ask",
    "Bid_Volume"
    "Ask_Volume"
)


def parse_arguments() -> argparse.Namespace:
    # read the user's choices from the command line
    # argparse converts text (ie --gap-seconds 90) into named python values
    # keeping the few important assumptions visible makes a run easier to
    # repeat and explain

    parser = argparse.ArgumentParser(
        description = __doc__,
        formatter_class = argparse.RawDescriptionHelpFormatter
        )

    parser.add_argument("--input", type = Path, 
        help = "collector CSV; required unless --self-test is used")
    
    parser.add_argument("--output", type=Path, default=Path("simple-ofi-run"), 
        help="new folder for this run")
    
    parser.add_argument("--overwrite", action="store_true", 
        help="replace an existing output folder")
    
    parser.add_argument("--self-test", action="store_true", 
        help="run small known-answer checks and exit")
    
    parser.add_argument("--gap-seconds", type=float, default=90, 
        help="a longer interval starts a new segment")
    
    parser.add_argument("--horizon-minutes", type=float, default=1, 
        help="how far ahead the target looks")
    
    parser.add_argument("--tolerance-seconds", type=float, default=30, 
        help="how late a future quote may be")
    
    parser.add_argument("--train-fraction", type=float, default=0.70, 
        help="earlier fraction used for training")

    
    return parser.parse_args()


# give the frozen input file a reproducible fingerprint
# reads the file in small chunks and update a SHA-256 digest 
# if even one byte changes, the hash almost certainly changes
# can then identify exactly which dataset produced the result

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



#calculate OFI for single row
def calculate_ofi(
    bid: float,
    ask: float,
    bid_volume: float,
    ask_volume: float,
    previous_bid: float,
    previous_ask: float,
    previous_bid_volume: float,
    previous_ask_volume: float
) -> float:
    '''
    measure the change in displayed buying vs selling pressure
    a higher bid or more size @ unchanged bid adds bid pressure, a lower bid removes it
    a lower ask or more size @ unchanged ask adds ask pressure.
    OFI equals bid-side event minus the ask-side event

    the calculation summarizes changes at the best displayed prices into
    one feature known at the current snapshot
    thus, 'snapshot' OFI
    '''
    
    if bid > previous_bid:
        bid_event = bid_volume
    elif bid < previous_bid:
        bid_event = -previous_bid_volume
    else:
        bid_event = bid_volume - previous_bid_volume

    if ask < previous_ask:
        ask_event = ask_volume
    elif ask > previous_ask:
        ask_event = -previous_ask_volume
    else:
        ask_event = ask_volume - previous_ask_volume


    return bid_event - ask_event


def audit_segment_and_add_ofi(raw: pd.DataFrame, 
    gap_seconds: float) -> tuple[pd.DataFrame, pd.DataFrame, dict]:

    '''
    clean the observations and stop calculations from crossing bad data
    parse the timestamps and numbers, record a reason for any invalid row,
    sort each item chronologically. 
    
    A new segment begins after an invalid row
    or a gap longer than the defined gap_sconds. OFI is calculated only
    between adjacent valid rows of the same segment.

    Dropping a bad row first could make two observations on different sides of
    an outage appear adjacent. Segmenting the data prevents that.
    '''


    missing = [column for column in REQUIRED_COLUMNS if column not in raw.columns]

    if missing:
        raise ValueError(f"missing required column(s): {','.join(missing)}")

    data = raw.copy()

    data["Source_Row"] = np.arange(2, len(data) + 2)
    data["Timestamp"] = pd.to_datetime(data["Timestamp"], utc=True, errors="coerce")
    data["Item"] = data["Item"].astype("string").str.strip()

    for column in REQUIRED_COLUMNS[2:]:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    data = data.sort_values(["Item", "Timestamp", "Source_Row"], 
        na_position="last").reset_index(drop=True)

    duplicate = data.duplicated(["Item", "Timestamp"],
        keep = False) & data["Timestamp"].notna()
    
    numeric = data[list(REQUIRED_COLUMNS[2:])].to_numpy(float)
    finite = np.isfinite(numeric).all(axis=1)

    reasons: list[list[str]] = [[] for _ in range(len(data))]


    def mark(mask: np.ndarray | pd.Series, reason:str) -> None:

        '''
        attach a readable problem label to selected rows
        convert true/false mask into row positions and append the text
        also accounts for the possibility of multiple problems in the same row
        '''

        for index in np.flatnonzero(np.asarray(mask)):
            reasons[index].append(reason)

    mark(data["Timestamp"].isna(), "invalid timestamp")
    mark(data["Item"].isna() | data["Item"].eq(""), "missing item")
    mark(~finite, "missing or non-finite number")
    mark(duplicate, "duplicate item and timestamp")
    mark(finite & ((data["Top_Bid"] <= 0) | (data["Top_Ask"] <= 0)), "non-positive price")
    mark(finite & ((data["Bid_Volume"] < 0) | (data["Ask_Volume"] < 0)), "negative volume")
    mark(finite & (data["Top_Bid"] > data["Top_Ask"]), "bid above ask")


    data["Rejection_Reasons"] = ["; ".join(row_reasons) for row_reasons in reasons]
    data["Valid"] = data["Rejection_Reasons"].eq("")
    data["Segment"] = pd.Series(pd.NA, index=data.index, dtype="Int64")

    for _, positions in data.groupby("Item", sort=False, dropna=False).indices.items():
        segment = 0
        previous_time = None
        previous_was_valid = False

        for position in positions:
            current_time = data.at[position, "Timestamp"]
            current_is_valid = bool(data.at[position, "Valid"])
            if not current_is_valid:
                previous_was_valid = False
                previous_time = current_time if pd.notna(current_time) else None

                continue

            elapsed = None if previous_time is None else (
                current_time - previous_time).total_seconds()
            if not previous_was_valid or elapsed is None or not (
                0 < elapsed <= gap_seconds):
                segment += 1

            data.at[position, "Segment"] = segment
            previous_time = current_time
            previous_was_valid = True

    rejected = data.loc[~data["Valid"], ["Source_Row", *REQUIRED_COLUMNS, "Rejection_Reasons"]].copy()

    valid = data.loc[data["Valid"]].copy()
    valid["Segment"] = valid["Segment"].astype(int)      

    valid["Mid_Price"] = (valid["Top_Bid"] + valid["Top_Ask"]) / 2 

    valid["OFI"] = np.nan

    for _, positions in valid.groupby(["Item","Segment"],
        sort=False).indices.items():
        group = valid.loc[positions].sort_values("Timestamp")
        previous = None

        for position, row in group.iterrows():
            if previous is not None:
                valid.at[position,"OFI"] = calculate_ofi(
                    row["Top_Bid"],
                    row["Top_Ask"],
                    row["Bid_Volume"],
                    row["Ask_Volume"],
                    previous["Top_Bid"],
                    previous["Top_Ask"],
                    previous["Bid_Volume"],
                    previous["Ask_Volume"],
                )
            previous = row

    reason_counts: dict[str, int] = {}
    for row_reasons in reasons:
        for reason in row_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    summary = {
        "input_rows": len(data),
        "valid_rows": len(valid),
        "rejected_rows": len(rejected),
        "items": valid["Item"].nunique(),
        "segments": valid.groupby(["Item", "Segment"]).ngroups,
        "rejection_reason_counts": reason_counts
    }

    return valid.reset_index(drop = True), rejected, summary


def add_future_return(valid: pd.DataFrame, horizon_minutes: float,
    tolerance_seconds: float) -> pd.DataFrame:

    '''
    define the outcome the model is trying to predict

    for every current row, find the first row in the same segment at or after
    current time plus the horizon. accept it only when it is no more than the stated
    tolerance late. return is the midpoint percentage change scaled into basis points
    10,000 * (future/current - 1)

    a row shift implies its the next observation not necessarily that it's a minute later
    '''

    result = valid.copy()
    result["Target_Timestamp"] = pd.Series(pd.NaT,
         index=result.index, dtype = "datetime64[ns, UTC]")
    result["Actual_Horizon_Seconds"] = np.nan
    result["Future_Return_Bps"] = np.nan

    horizon_seconds = horizon_minutes * 60

    for _, positions in result.groupby(["Item", "Segment"],
        sort = False).indices.items():
        group = result.loc[positions].sort_values("Timestamp")
        group_positions = group.index.to_numpy()
        times = group["Timestamp"].to_numpy(dtype = "datetime64[ns]").astype("int64")

        midpoints = group["Mid_Price"].to_numpy(float)
        wanted = times + int(horizon_seconds * 1_000_000_000)
        future_indices = np.searchsorted(times, wanted, side = "left")

        for current_index, future_index in enumerate(future_indices):
            if future_index >= len(group):
                continue

            actual_seconds = (times[future_index] - times[current_index]) / 1_000_000_000

            if actual_seconds > horizon_seconds + tolerance_seconds:
                continue

            output_position = group_positions[current_index]
            result.at[output_position, "Target_Timestamp"] = group.iloc[future_index]["Timestamp"]
            result.at[output_position, "Actual_Horizon_Seconds"] = actual_seconds
            result.at[output_position, "Future_Return_Bps"] = 10_000 * (
                midpoints[future_index] / midpoints[current_index] - 1
            )

    return result


def fit_line(x: np.ndarray, y: np.ndarray) -> dict | None:
    '''
    learn a simple relationship from training OFI and future returns
    fit "predicted return = intercept + slope * OFI" using the ordinary
    least squares formulas.

    A straight line is transparent enough to calculate & explain. Returning
    None for constant or tiny data is better than inventing an inaccurate fit
    '''

    usable = np.isfinite(x) & np.isfinite(y)
    x, y = x[usable], y[usable]

    if len(x) < 2 or np.ptp(x) == 0:
        return None

    x_mean = float(x.mean())
    y_mean = float(y.mean())
    slope = float(np.sum((x - x_mean) * (y - y_mean)
    ) / np.sum((x - x_mean)** 2))

    return {"intercept": y_mean - slope * x_mean,
        "slope": slope,
        "training_rows": len(x),
        "training_mean_return": y_mean
        }


def safe_correlation(x: np.ndarray, y: np.ndarray) -> float:

    '''
    summarize the linear association without a false value
    calculate pearson correlation only when both arrays vary and contain
    at least two finite pairs, otherwise return undefined
    '''

    usable = np.isfinite(x) & np.isfinite(y)
    x, y = x[usable], y[usable]
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return math.nan
    return float(np.corrcoef(x, y)[0, 1])


def train_and_test(labeled: pd.DataFrame, train_fraction: float) -> tuple[
    pd.DataFrame, pd.DataFrame, pd.Timestamp]:

    '''
    use earlier data to learn, then test untouched later data

    choose one shared chronological cutoff across all items. For each item,
    train the line only on earlier features whos future target ends before
    the cutoff. freeze the line and predict later rows.

    '''

    usable = labeled.dropna(subset=["OFI",
        "Future_Return_Bps",
        "Target_Timestamp"]
        ).sort_values("Timestamp")
    
    if usable.empty:
        raise ValueError("no rows have both OFI and an accepted future target")
    
    times = usable["Timestamp"].drop_duplicates().sort_values().reset_index(drop=True)
    split_index = min(len(times) - 1, max(1, int(len(times) * train_fraction)))

    cutoff = times.iloc[split_index]
    predictions: list[pd.DataFrame] = []
    summaries: list[dict] = []

    for item, group in usable.groupby("Item", sort=True):
        train = group[(group["Timestamp"] < cutoff) & (group["Target_Timestamp"] < cutoff)]
        test = group[group["Timestamp"] >= cutoff].copy()

        model = fit_line(train["OFI"].to_numpy(float), 
            train["Future_Return_Bps"].to_numpy(float))
        
        if model is None or len(test) < 2:
            summaries.append({"Item": item,
                "Status": "insufficient varying training or test data", 
                "Train_Rows": len(train),
                "Test_Rows": len(test)})
            
            continue

        test["Predicted_OFI_Return_Bps"] = model["intercept"] + model["slope"] * test["OFI"]
        test["Predicted_Zero_Return_Bps"] = 0.0
        test["Predicted_Train_Mean_Bps"] = model["training_mean_return"]
        actual = test["Future_Return_Bps"].to_numpy(float)
        ofi_prediction = test["Predicted_OFI_Return_Bps"].to_numpy(float)
        zero_prediction = test["Predicted_Zero_Return_Bps"].to_numpy(float)
        mean_prediction = test["Predicted_Train_Mean_Bps"].to_numpy(float)
        summaries.append(
            {
                "Item": item,
                "Train_Rows": len(train),
                "Test_Rows": len(test),
                "Train_Last_Target": train["Target_Timestamp"].max(),
                "Test_First_Feature": test["Timestamp"].min(),
                "Intercept_Bps": model["intercept"],
                "Slope_Bps_Per_OFI_Unit": model["slope"],
                "Test_Zero_Return_Rate": float(np.mean(actual == 0)),
                "Test_OFI_Return_Correlation": safe_correlation(test["OFI"].to_numpy(float), actual),
                "OFI_MAE_Bps": float(np.mean(np.abs(actual - ofi_prediction))),
                "Zero_Baseline_MAE_Bps": float(np.mean(np.abs(actual - zero_prediction))),
                "Mean_Baseline_MAE_Bps": float(np.mean(np.abs(actual - mean_prediction))),
                "OFI_MSE_Bps_Squared": float(np.mean((actual - ofi_prediction) ** 2)),
                "Zero_Baseline_MSE_Bps_Squared": float(np.mean((actual - zero_prediction) ** 2)),
                "Mean_Baseline_MSE_Bps_Squared": float(np.mean((actual - mean_prediction) ** 2)),
            }
        )

        predictions.append(test)

    combined = pd.concat(predictions, ignore_index=True) if  predictions else pd.DataFrame()

    return combined, pd.DataFrame(summaries), cutoff





            




#most of the program in main(), excluding imports and functions
#this lets tests import the calculations without running the entire program
def main():
    BASE_DIR = Path(__file__).resolve().parent.parent



    #pandas data frame
    #read csv file with all scraped bazaar data
    df = pd.read_csv(BASE_DIR / "data" / 'bazaar_quotes_utc.csv')

    df['Timestamp'] = pd.to_datetime(df['Timestamp'], utc=True, errors='raise')
    #sorts item in df by timestamp and item (chronological per-item order)
    #and reassigns to df
    df = df.sort_values(by=['Item', 'Timestamp']).reset_index(drop=True)
    if df.duplicated(["Item", "Timestamp"]).any():
        raise ValueError("duplicate item/timestamp rows; check before analysis")

    #checking for any potential errors
    #missing values, crossed quotes, negative prices/volumes
    cols = ['Top_Bid', 'Top_Ask', 'Bid_Volume', 'Ask_Volume']

    if not np.isfinite(df[cols].to_numpy(dtype=float)).all():
        raise ValueError('Missing or nonfinite quote/volume values')
    
    if (df['Top_Bid'] > df['Top_Ask']).any():
        raise ValueError('crossed quotes')
    
    if (df[["Top_Bid", "Top_Ask"]] <= 0).any().any():
        raise ValueError('non-positive prices')
    
    if (df[["Bid_Volume", "Ask_Volume"]] < 0).any().any():
        raise ValueError('negative volume')


    #check actual time gaps and add columns for each
    df['Prev_Gap_Actual'] = df.groupby('Item')['Timestamp'].diff().dt.total_seconds()
    df['Next_Timestamp'] = df.groupby('Item')['Timestamp'].shift(-1)
    df['Next_Gap_Actual'] = (df['Next_Timestamp'] - df['Timestamp']).dt.total_seconds()


    '''
    OFI measures whether buyers (bids) or sellers (asks) are
    more aggressive. using pandas, i'll compare the current row to
    the previous row
    '''

    #isolate previous states by creating new columns
    #repeat for each value
    df['Prev_Top_Bid'] = df.groupby('Item')['Top_Bid'].shift(1)
    df['Prev_Top_Ask'] = df.groupby('Item')['Top_Ask'].shift(1)

    df['Prev_Bid_Vol'] = df.groupby('Item')['Bid_Volume'].shift(1)
    df['Prev_Ask_Vol'] = df.groupby('Item')['Ask_Volume'].shift(1)


    #apply function to every row in df
    # axis=1 means row by row
    df['OFI'] = df.apply(calculate_ofi, axis=1)


    #mid-price used as real 'current value'
    #define prediction target
    df['Mid_Price'] = (df['Top_Bid']+df['Top_Ask'])/2
    df['Next_Mid_Price'] = df.groupby('Item')['Mid_Price'].shift(-1)


    #get rid of rows where gap between fetches is too large to use
    #has to run at end to make sure .shift() isn't running on filtered data w/ gaps

    GAP_THRESHOLD = 90
    df = df[
        df['Prev_Gap_Actual'].between(1, GAP_THRESHOLD)
        & df['Next_Gap_Actual'].between(1, GAP_THRESHOLD)
        ].copy()


    ### ACTUAL TESTING

    df['Future_Price_Change'] = df['Next_Mid_Price'] - df['Mid_Price']

    #drop first & last rows since some data will be empty due to shifting
    df = df.dropna(subset=['OFI', 'Future_Price_Change'])

    #split 70/30 to avoid lookahead bias
    #find row index representing 70 percent of data
    #split data chronologically
    #using .iloc (index location)

    train_parts = []
    test_parts = []

    for item, group in df.groupby('Item'):
        #group sorted by time already
        split_point = int(len(group) * 0.70)
        train_parts.append(group.iloc[:split_point])
        test_parts.append(group.iloc[split_point:])

    train_data = pd.concat(train_parts)
    test_data = pd.concat(test_parts)

    results = []
    for item, g in test_data.groupby('Item'):
        r, p = stats.pearsonr(g['OFI'], g['Future_Price_Change'])
        results.append({'item': item, 'n': len(g), 'corr': r, 'pval': p})


    #calculate correlation on data from outside sample
    correlation = test_data['OFI'].corr(test_data['Future_Price_Change'])
    print(f"OFI to Future Price Change Correlation: {correlation:.4f}")
    #if correlation is positive, higher OFI generally leads to a price increase


    #visualize the correlation, p-value, and n

    fig, ax = plt.subplots(figsize=(8,6))

    for row in results:
        ax.scatter(row['corr'], -np.log10(row['pval']), s=80)
        ax.annotate(row['item'], (row['corr'], -np.log10(row['pval'])),
                    textcoords="offset points", xytext=(5, 5), fontsize=8)

    n_tests = len(results)
    ax.axhline(-np.log10(0.05), color='gray', linestyle='--', label='p = 0.05')
    ax.axhline(-np.log10(0.05 / n_tests), color='red', linestyle='--',
            label=f'Bonferroni threshold (p = {0.05/n_tests:.4f})')
    ax.axvline(0, color='black', linewidth=0.8)

    ax.set_xlabel('OFI - Future Price Change Correlation')
    ax.set_ylabel('-log10(p-value)')
    ax.legend()
    plt.tight_layout()

    #outputs to results directory
    OUTPUT_DIR = BASE_DIR / "results-v0.2"
    OUTPUT_DIR.mkdir(parents = True, exist_ok= True)

    if not os.path.exists(OUTPUT_DIR):
        print(f"output directory {OUTPUT_DIR} doesn't exist")
    else:
        print(f"output directory {OUTPUT_DIR} found, saving results")

    plt.savefig(OUTPUT_DIR / 'ofi_volcano_plot_oos.png', dpi=150)

    test_data.to_csv(OUTPUT_DIR / 'test_data.csv', index=False)
    results_df = pd.DataFrame(results)
    results_df.to_csv(OUTPUT_DIR / 'ofi_corr_summary.csv', index=False)


#execute main only if this specific script is directly executed
if __name__ == "__main__":
    main()