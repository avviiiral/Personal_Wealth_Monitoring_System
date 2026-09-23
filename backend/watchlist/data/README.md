# BSE 500 TRI benchmark data

This directory is reserved for the actual BSE 500 Total Return Index series.

**Authoritative source:** BSE / BSE Index Services' licensed BSE 500 Total Return Index (TRI). BSE's published methodology identifies the TRI vendor code as **BSE500T** (Bloomberg and Reuters). The public BSE daily-index archive used for the BSE 500 price index is not treated as a TRI source.

Do not put the BSE 500 price index into `bse500_tri.csv`.

Expected CSV shape:

    Date,Close
    2021-01-01,12345.67
    2021-01-04,12380.12

The loader rejects invalid dates, non-positive values, duplicate dates, non-monotonic dates, and missing values. Weekends/holidays should simply be absent, matching the source export.

The repository intentionally does not fabricate or reconstruct TRI values from the price index. A licensed/exported BSE 500 TRI file must be supplied before the benchmark can report availability.