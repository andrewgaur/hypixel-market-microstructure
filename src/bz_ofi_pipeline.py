import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from pathlib import Path



#calculate OFI for single row
def calculate_ofi(row):

    #bid side pressure
    if row['Top_Bid'] > row['Prev_Top_Bid']:
        e_n = row['Bid_Volume']
    elif row['Top_Bid'] < row['Prev_Top_Bid']:
        e_n = -row['Prev_Bid_Vol']
    else:
        e_n = row['Bid_Volume'] - row['Prev_Bid_Vol']

    #ask side pressure
    if row['Top_Ask'] < row['Prev_Top_Ask']:
        f_n = row['Ask_Volume']
    elif row['Top_Ask'] > row['Prev_Top_Ask']:
        f_n = -row['Prev_Ask_Vol']
    else:
        f_n = row['Ask_Volume'] - row['Prev_Ask_Vol']

    return e_n - f_n


#most of the program in main(), excluding imports and functions
#this lets tests import the calculations without running the entire program
def main():
    BASE_DIR = Path(__file__).resolve().parent.parent



    #pandas data frame
    #read csv file with all scraped bazaar data
    df = pd.read_csv(BASE_DIR / "data" / 'bazaar_raw_data2.csv')
    #sorts item in df by timestamp and item (chronological per-item order)
    #and reassigns to df
    df = df.sort_values(by=['Item', 'Timestamp'])


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
    df['Next_Gap'] = df.groupby('Item')['Seconds_Since_Fetch'].shift(-1)
    df = df[(df['Seconds_Since_Fetch'] <= GAP_THRESHOLD) & (df['Next_Gap'] <= GAP_THRESHOLD)]


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
    plt.savefig(BASE_DIR / "results-fix" / 'ofi_volcano_plot_oos.png', dpi=150)

    test_data.to_csv(BASE_DIR / "results-fix" / 'test_data.csv', index=False)
    results_df = pd.DataFrame(results)
    results_df.to_csv(BASE_DIR / "results-fix" / 'ofi_corr_summary.csv', index=False)


#execute main only if this specific script is directly executed
if __name__ == "__main__":
    # main()
    pass