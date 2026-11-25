## Every condition, on frankfurter, pokeapi, postcodes_io

| Condition | Runs | Success (min / mean) | Grounded success | Tool calls | Tokens | Cost per task | Invalid calls |
|---|---|---|---|---|---|---|---|
| A (OpenAPI direct) | 180 | 93.3% / 95.0% | 95.0% | 1.73 | 33,511 | $0.0035 | 5% |
| B (docs2mcp direct) | 180 | 96.7% / 97.8% | 97.8% | 1.76 | 29,228 | $0.0031 | 3% |
| C (docs2mcp task-shaped) | 180 | 98.3% / 98.3% | 98.3% | 1.61 | 26,065 | $0.0027 | 1% |
| D (FastMCP from OpenAPI) | 60 | 90.0% / 90.0% | 83.3% | 3.48 | 69,663 | $0.0072 | 13% |

## By level, on the same APIs

| Level | A | B | C | D |
|---|---|---|---|---|
| single | 100% | 100% | 100% | 100% |
| multi | 96% | 96% | 96% | 88% |
| complex | 83% | 97% | 100% | 75% |

## Direct (B) and task-shaped (C), on every API

| API | Runs | Success (min / mean) | Grounded success | Tool calls | Tokens | Cost per task | Invalid calls |
|---|---|---|---|---|---|---|---|
| frankfurter B | 60 | 100.0% / 100.0% | 100.0% | 2.20 | 13,244 | $0.0015 | 8% |
| frankfurter C | 60 | 100.0% / 100.0% | 100.0% | 1.98 | 14,346 | $0.0016 | 3% |
| open_library B | 60 | 85.0% / 85.0% | 85.0% | 2.92 | 24,856 | $0.0027 | 3% |
| open_library C | 60 | 70.0% / 78.3% | 78.3% | 3.92 | 41,585 | $0.0045 | 2% |
| open_meteo B | 36 | 100.0% / 100.0% | 91.7% | 1.33 | 7,806 | $0.0009 | 29% |
| open_meteo C | 36 | 100.0% / 100.0% | 91.7% | 1.03 | 7,043 | $0.0008 | 8% |
| open_meteo_geocoding B | 24 | 100.0% / 100.0% | 100.0% | 1.12 | 3,589 | $0.0004 | 0% |
| open_meteo_geocoding C | 24 | 100.0% / 100.0% | 100.0% | 1.12 | 3,441 | $0.0004 | 0% |
| pokeapi B | 60 | 95.0% / 98.3% | 98.3% | 1.62 | 62,895 | $0.0064 | 0% |
| pokeapi C | 60 | 100.0% / 100.0% | 100.0% | 1.53 | 52,717 | $0.0054 | 0% |
| postcodes_io B | 60 | 95.0% / 95.0% | 95.0% | 1.47 | 11,545 | $0.0013 | 0% |
| postcodes_io C | 60 | 95.0% / 95.0% | 95.0% | 1.30 | 11,130 | $0.0012 | 0% |
| all B | 300 | 95.0% / 95.7% | 94.7% | 1.89 | 23,732 | $0.0025 | 5% |
| all C | 300 | 93.0% / 94.7% | 93.7% | 1.96 | 25,076 | $0.0027 | 2% |

## Tasks that failed or were not grounded

| Task | Runs |
|---|---|
| frankfurter-012 | D1 no answer: `` |
| frankfurter-018 | A1 wrong: `2022-03-15`; A3 wrong: `2022-03-15`; D1 wrong: `2022-03-15` |
| open_library-004 | C1 wrong: `Houghton Mifflin Harcourt` |
| open_library-005 | C1 no answer: ``; C2 wrong: `Unable to determine`; C3 step limit: `` |
| open_library-015 | C1 wrong: `Foundation by Isaac Asimov` |
| open_library-016 | B1 wrong: `OL27513W`; B2 wrong: `OL10236417M`; B3 no answer: ``; C1 no answer: ``; C2 no answer: ``; C3 wrong: `Not determined; the ISBN belongs to the ` |
| open_library-017 | B1 wrong: `Things Fall Apart (1958)`; B2 wrong: `Things Fall Apart (1958)`; B3 wrong: `Things Fall Apart (1958)`; C1 wrong: `Things Fall Apart (1958)`; C3 wrong: `Things Fall Apart (1958)` |
| open_library-020 | B1 wrong: `OL27513W`; B2 wrong: `OL27513W`; B3 wrong: `OL27513W`; C1 wrong: `OL27513W`; C2 wrong: `OL27513W`; C3 wrong: `OL27513W` |
| open_meteo-003 | B1 not grounded: `km/h`; B2 not grounded: `km/h`; B3 not grounded: `km/h`; C1 not grounded: `km/h`; C2 not grounded: `km/h`; C3 not grounded: `km/h` |
| pokeapi-017 | A1 wrong: `bug, fire, flying, ice, psychic`; B3 wrong: `flying, fire, ice, ground, psychic` |
| postcodes_io-005 | D1 not grounded: `Yorkshire and The Humber` |
| postcodes_io-008 | D1 not grounded: `Edinburgh Central` |
| postcodes_io-009 | D1 wrong: `SW1A 2AA` |
| postcodes_io-010 | D1 wrong: `SW1A 1AA, SW1A 1AB, SW1A 1AD` |
| postcodes_io-014 | A1 no answer: ``; A2 no answer: ``; A3 no answer: ``; B1 no answer: ``; B2 no answer: ``; B3 wrong: ``; C1 no answer: ``; C2 no answer: ``; C3 no answer: `` |
| postcodes_io-015 | D1 not grounded: `G1 1XQ` |
| postcodes_io-016 | D1 not grounded: `CB2` |
| postcodes_io-018 | D1 step limit: `` |
| postcodes_io-019 | A1 wrong: `CA12 5LP`; A2 wrong: `CA12 4NA`; A3 wrong: `CA12 5HP`; D1 wrong: `CA12 5JR` |
