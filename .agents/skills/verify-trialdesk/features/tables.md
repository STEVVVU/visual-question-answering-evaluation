# Workspace tables

## Sub-features

Synthetic upload, workspace identity, Parquet persistence and exact row reads after restart.

## How to get to it (user POV)

Open the application, choose the verification session, then Upload data → Upload Data → Browse. Select `tools/fixtures/synthetic-experiment.csv`, inspect its four-column preview and press Load Table.

## Driving it with HTTP and CUA

Run the maintained helper, send `drive`, `restart`, `verify`, then `cleanup`. It compares all 120 synthetic records and inherited row positions through real routes. CUA can independently import the 1,000-row CSV through the actual file chooser. Preserve the upload preview and result observations.

## Gotchas

The inherited main canvas requires a model and renders an overlay when none is selected. Without a provider, semantic-type and starter-question calls fail. The upload and persistent table API are usable; this is not a model-workflow or unobscured-canvas acceptance pass. Never configure credentials merely to remove the overlay.
