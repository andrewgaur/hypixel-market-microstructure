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
    "Bid_Volume",
    "Ask_Volume",
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


def save_plot(predictions: pd.DataFrame, output: Path) -> None:

    '''
    make the test results easier to visually inspect

    if matplotlib is installed, draw actual vs predicted returns for each item.
    the diagonal reference line represents perfect predictions
    '''

    if predictions.empty:
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

    except ImportError:
        (output / "plot_not_created.txt").write_text("install matplotlib to create" \
        "the optional chart.\n", encoding="utf-8")
        return

    items = sorted(predictions["Item"].unique())
    columns = 2
    rows = math.ceil(len(items) / columns)

    figure, axes = plt.subplots(rows,
        columns,
        figsize = (10, 4 * rows),
        squeeze = False )

    for axis, item in zip(axes.flat, items):
        group = predictions[predictions["Item"] == item]
        actual = group["Future_Return_Bps"]
        predicted = group["Predicted_OFI_Return_Bps"]

        axis.scatter(actual, predicted, s=8, alpha=0.45)

        low = min(actual.min(), predicted.min())
        high = max(actual.max(), predicted.max())

        axis.plot([low, high], [low, high], "--", color="black", linewidth=1)

        axis.set_title(item)
        axis.set_xlabel("Actual future return (bp)")
        axis.set_ylabel("Predicted future return (bp)")

    for axis in axes.flat[len(items) :]:
        axis.set_visible(False)

    figure.suptitle("Held-out predictions versus outcomes")
    figure.tight_layout()

    figure.savefig(output / "predictions_vs_actual.png", dpi=150)

    plt.close(figure)


def self_test() -> None:

    '''
    catch mistakes with tiny known examples. checks hand-calculated OFI (+9),
    create intentional collection gap, verify neither OFI nor a future target crosses it.
    '''

    assert calculate_ofi(99, 101, 15, 8, 99, 101, 10, 12) == 9
    raw = pd.DataFrame(
        {
            "Timestamp": pd.to_datetime(
                ["2026-01-01T00:00:00Z", 
                "2026-01-01T00:01:00Z", 
                "2026-01-01T00:02:00Z", 
                "2026-01-01T00:10:00Z", 
                "2026-01-01T00:11:00Z"]
                ),
            "Item": "TEST",
             "Top_Bid": [99] * 5,
            "Top_Ask": [101] * 5,
            "Bid_Volume": [10, 15, 16, 20, 21],
            "Ask_Volume": [12, 8, 8, 10, 10],
        }
    )

    valid, rejected, _ = audit_segment_and_add_ofi(raw, 90)

    assert rejected.empty

    assert valid["Segment"].tolist() == [1,1,1,2,2]
    assert math.isnan(valid.loc[3, "OFI"])

    labeled = add_future_return(valid, 1, 1)
    assert pd.isna(labeled.loc[2, "Target_Timestamp"])

    print("self-test passed")

def main() -> int:
    '''
    run the complete small analysis in a clear order

    validate settings, create frozen input copy, call each function,
    save evidence as CSV or JSON in one new output folder


    '''

    arguments = parse_arguments()
    if arguments.self_test:
        self_test()
        return 0

    if arguments.input is None:
        raise SystemExit("--input is required unless --self-test is used")

    if arguments.gap_seconds <= 0 or arguments.horizon_minutes <= 0 or (
        arguments.tolerance_seconds < 0 ):

        raise SystemExit("gap and horizon must be positive; tolerance can't be negative")

    if not 0.5 <= arguments.train_fraction < 1:
        raise SystemExit("--train-fraction must be atleast 0.5 and less than 1")

    input_path = arguments.input.expanduser().resolve()
    output = arguments.output.expanduser().resolve()

    if not input_path.is_file():
        raise SystemExit(f"input CSV not found: {input_path}")
    
    if output.exists():
        if not arguments.overwrite:
            raise SystemExit(f"output already exists: {output}; use --overwrite deliberately")

        if output == Path(output.anchor) or input_path.is_relative_to(output):
             raise SystemExit("refusing to replace a filesystem root or folder containing the input")

        shutil.rmtree(output)

    output.mkdir(parents=True)

    
    snapshot = output / "input_snapshot.csv"
    shutil.copyfile(input_path, snapshot)
    run_info = {
        "status": "started",
        "source_input": str(input_path),
        "analyzed_snapshot": str(snapshot),
        "input_sha256": sha256(snapshot),
        "gap_seconds": arguments.gap_seconds,
        "horizon_minutes": arguments.horizon_minutes,
        "tolerance_seconds": arguments.tolerance_seconds,
        "train_fraction": arguments.train_fraction,
    }

    try:
        raw = pd.read_csv(snapshot)

        valid, rejected, quality = audit_segment_and_add_ofi(raw, arguments.gap_seconds)
        labeled = add_future_return(valid, arguments.horizon_minutes, arguments.tolerance_seconds)
        predictions, summary, cutoff = train_and_test(labeled, arguments.train_fraction)

        rejected.to_csv(output / "rejected_rows.csv", index=False)

        labeled.to_csv(output / "analyzed_rows.csv", index=False)
        predictions.to_csv(output / "test_predictions.csv", index=False)

        summary.to_csv(output / "results_summary.csv", index=False)
        save_plot(predictions, output)

        run_info.update({"status": "complete", 
            "quality": quality, 
            "train_test_cutoff_utc": cutoff.isoformat()
            })

    except Exception as error:
        run_info.update({"status": "failed", "error": f"{type(error).__name__}: {error}"})
        raise
    finally:
        (output / "run_info.json").write_text(json.dumps(run_info, indent=2, default=str), encoding="utf-8")
    print(f"complete: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

