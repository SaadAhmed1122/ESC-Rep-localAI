# KPI catalog and data format

The catalog lives in [`src/esglocal/data/kpi_catalog.yaml`](../src/esglocal/data/kpi_catalog.yaml).
It is written for this project and released under the MIT license. Framework references
(GRI, ESRS, EU Taxonomy) are indicative mappings, not an official crosswalk.

## KPIs

| Category | KPIs |
|---|---|
| Environmental | Energy intensity · Renewable share · GHG intensity (S1+2) · GHG total (reported, not scored) · GHG YoY reduction · Water intensity · Waste recycled · Hazardous waste share · *Climate transition plan (narrative)* |
| Social | Turnover · Training hours/FTE · Training spend/FTE · LTIFR · Fatalities · Women in management · Gender pay gap · Customer satisfaction · *Human rights due diligence (narrative)* |
| Governance | Board independence · Women on board · Revenue in low corruption-risk regions · Political contributions · Fines · Sustainable CapEx · Supplier ESG screening · Executive pay linked to ESG · *Anti-corruption & whistleblowing (narrative)* |

Run `esglocal score sample` to see every KPI with its formula inputs and frameworks.

## Scoring

- Each scored KPI is normalised to **0–100**: `worst → 0`, `best → 100`, linear, clamped.
  The direction comes from the benchmark (`best > worst` means higher is better).
- A category score is the mean of its scored KPIs.
- The overall score is the materiality-weighted mean of the categories, with the weights
  re-normalised over the categories that have data.
- Ratings: Leader ≥ 80 · Advanced ≥ 60 · Developing ≥ 40 · Lagging ≥ 20 · Critical.
- Narrative KPIs are scored by how close the text is to the best-practice exemplar versus
  the worst-practice exemplar.

## Data format

Use one row per period. A `Year` / `Period` / `FY` column is detected automatically. Several
rows per period (for example one per site) are aggregated: flows are summed, and stock values
(board size, average pay) are averaged. Column names do not need to match exactly. For
example, `Scope 1 emissions (tCO2e)` maps to `scope1_tco2e`.

Download a blank template from **Data & KPIs → Upload**, or run `esglocal sample --out my.csv`.

## Customising

- **Benchmarks:** override them per workspace in **Data & KPIs → Benchmarks**, or per industry in the YAML.
- **New KPI:** add an entry with `id`, `name`, `category`, `formula`, `inputs`, `benchmark` and
  `frameworks`. Formulas may only use `+ - * / ( )`, numbers, declared inputs and
  `min / max / abs`. The catalog is validated on load.
- **Custom catalog file:** `esglocal score data.csv --industry X --catalog my_catalog.yaml`.
