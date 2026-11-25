#!/usr/bin/env bash
# Rebuild every table number of the paper from the released run logs. Run from the paper/ folder:
#   bash reeval/rebuild_tables.sh
# It unpacks runs_release/*.tar.gz into runs/, recomputes reeval/paper_results/table_data.json (first round)
# and phase2_stats.json (second round), and renders the LaTeX tables into out/.
# The frontier and Nemotron control-arm values are counts only (reeval/paper_results/frontier_runs.json and
# nemotron_external.json), because those runs were made on a company platform whose logs cannot be released.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p runs out
for f in runs_release/*.tar.gz; do tar xzf "$f" -C runs; done
python3 reeval/paper_tables_data.py
python3 reeval/phase2_analysis.py
python3 reeval/paper_tables_render.py reeval/paper_results/table_data.json out/
echo "Done. Compare reeval/paper_results/*.json with the committed versions (git diff), and see out/ for the tables."
