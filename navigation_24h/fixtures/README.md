# Test fixtures

`geometry/` contains curated AMap MCP and REST responses for two public Shenzhen Futian routes, plus documented quota responses. `routes/candidate_route01.json` contains a public Hong Kong campus route summary. Geometry and ordered step metadata retain their provider values.

`poi/` contains public Hong Kong City University search and entrance details. The replay handshake uses a deterministic protocol fixture. HTTP status, JSON content type, RPC correlation, and the fields consumed by the production parser form the fixture boundary.

`tests/coordinates/reference_points.json` records the pinned coordinate algorithm reference used by coordinate tests. Third party source attribution accompanies the firmware components.
