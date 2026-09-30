"""Column profiling and anomaly detection - what the data is like, run over run.

Checks derived from a pipeline's own DDL can only assert that the pipeline did
what its code says. When the code is wrong, the check is wrong with it: the
inventory MERGE reads `RAW_PAYLOAD:warehouse` when the key is `warehouse_id`,
and the parity check derived from that MERGE agrees with it. Profiling is
the half that needs nothing from the code - it measures the data itself and
compares each number against that column's own history.

Two layers, deliberately separate:

* **Findings** need no history. A column that is NULL on every row of a
  populated table is suspicious on the first run, and it is exactly the shape
  of the `WAREHOUSE_ID` bug - which has been 100% NULL since the day it was
  deployed, so a history-only detector would learn it as normal and never say
  a word.
* **Anomalies** need history. A null rate that has sat at 0.1% for thirty runs
  and is 40% today, a table that lands 40k rows a day and landed 900, a price
  column that has never been negative and now is. These are judged against a
  robust baseline (median and MAD), so one bad run does not poison it.

Profiling is separate from the check engine on purpose. A check is a
pass/fail assertion someone reviewed; a profile is an observation that is
interesting whether or not anyone set a threshold on it. Folding one into the
other would either make profiles block like checks or make checks as soft as
profiles.

What is stored is aggregate only: counts, ratios, numeric and temporal bounds,
and string *lengths*. No text value from a profiled table is ever written to
this database, so the profile history cannot leak the data it describes.
"""
