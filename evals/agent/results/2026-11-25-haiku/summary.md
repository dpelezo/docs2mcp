| API | Condition | Tasks x repeats | Success (min / mean) | Grounded success | Tool calls | Tokens | Invalid calls |
|---|---|---|---|---|---|---|---|
| frankfurter | A (OpenAPI direct) | 20 x 3 | 95% / 97% | 95% / 97% | 1.2 | 10,877 | 1% |
| frankfurter | B (docs2mcp direct) | 20 x 3 | 100% / 100% | 100% / 100% | 2.2 | 13,244 | 8% |
| frankfurter | C (docs2mcp task-shaped) | 20 x 3 | 100% / 100% | 100% / 100% | 2.0 | 14,346 | 3% |
| frankfurter | D (FastMCP from OpenAPI) | 20 x 1 | 90% / 90% | 90% / 90% | 1.2 | 9,457 | 4% |
| open_library | B (docs2mcp direct) | 20 x 3 | 85% / 85% | 85% / 85% | 2.9 | 24,856 | 3% |
| open_library | C (docs2mcp task-shaped) | 20 x 3 | 70% / 78% | 70% / 78% | 3.9 | 41,585 | 2% |
| open_meteo | B (docs2mcp direct) | 12 x 3 | 100% / 100% | 92% / 92% | 1.3 | 7,806 | 29% |
| open_meteo | C (docs2mcp task-shaped) | 12 x 3 | 100% / 100% | 92% / 92% | 1.0 | 7,043 | 8% |
| open_meteo_geocoding | B (docs2mcp direct) | 8 x 3 | 100% / 100% | 100% / 100% | 1.1 | 3,589 | 0% |
| open_meteo_geocoding | C (docs2mcp task-shaped) | 8 x 3 | 100% / 100% | 100% / 100% | 1.1 | 3,441 | 0% |
| pokeapi | A (OpenAPI direct) | 20 x 3 | 95% / 98% | 95% / 98% | 1.6 | 74,882 | 1% |
| pokeapi | B (docs2mcp direct) | 20 x 3 | 95% / 98% | 95% / 98% | 1.6 | 62,895 | 0% |
| pokeapi | C (docs2mcp task-shaped) | 20 x 3 | 100% / 100% | 100% / 100% | 1.5 | 52,717 | 0% |
| pokeapi | D (FastMCP from OpenAPI) | 20 x 1 | 100% / 100% | 100% / 100% | 1.6 | 169,401 | 3% |
| postcodes_io | A (OpenAPI direct) | 20 x 3 | 90% / 90% | 90% / 90% | 2.3 | 14,772 | 11% |
| postcodes_io | B (docs2mcp direct) | 20 x 3 | 95% / 95% | 95% / 95% | 1.5 | 11,545 | 0% |
| postcodes_io | C (docs2mcp task-shaped) | 20 x 3 | 95% / 95% | 95% / 95% | 1.3 | 11,130 | 0% |
| postcodes_io | D (FastMCP from OpenAPI) | 20 x 1 | 80% / 80% | 60% / 60% | 7.5 | 30,130 | 17% |
| all | A (OpenAPI direct) | 60 x 3 | 93% / 95% | 93% / 95% | 1.7 | 33,511 | 5% |
| all | B (docs2mcp direct) | 100 x 3 | 95% / 96% | 94% / 95% | 1.9 | 23,732 | 5% |
| all | C (docs2mcp task-shaped) | 100 x 3 | 93% / 95% | 92% / 94% | 2.0 | 25,076 | 2% |
| all | D (FastMCP from OpenAPI) | 60 x 1 | 90% / 90% | 83% / 83% | 3.5 | 69,663 | 13% |
