=

# AWS Terrarium DEM Tile Downloader

![Terrarium Tiles](public/terrarium.jpeg)

A command-line tool to download and verify Terrarium-encoded Digital Elevation Model (DEM) tiles from the AWS Open Data Terrain Tiles dataset. It allows users to fetch tiles for a specific geographic bounding box and zoom range, storing them in the standard `z/x/y` directory structure, and generates a compatible `tiles.json` metadata file.

## Features

*   **Download by Bounding Box:** Specify the geographic area using minimum/maximum longitude and latitude.
*   **Zoom Level Selection:** Define the desired zoom levels (0-15) for download or checking.
*   **Concurrent Downloads:** Utilizes multiple threads for significantly faster downloading of large tile sets.
*   **Tile Verification:** Checks existing tile sets for missing files and basic integrity (correct dimensions 256x256).
*   **`tiles.json` Generation:** Automatically creates a TileJSON metadata file compatible with map libraries like MapLibre GL JS, Leaflet, Mapbox GL JS, etc.
*   **Selective Downloading:** Option to only download tiles that are currently missing from the target directory.
*   **Reporting:** Generates JSON reports summarizing download and check operations, including lists of failed/missing tiles.
*   **Command-Line Interface:** Easy-to-use CLI built with Click.

## Requirements

*   Python 3.7+
*   Libraries listed in `requirements.txt`:
    *   `requests`
    *   `Pillow` (PIL Fork)
    *   `click`
    *   `tqdm`
    *   `urllib3`

## Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repository-url> # Or download the source code
    cd AWS-Dem-Downloader
    ```
2.  **Create a virtual environment (recommended):**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Make the script executable (Linux/macOS - optional):**
    ```bash
    chmod +x terrain_cli.py
    ```

## Usage

The tool is operated via the command line using `terrain_cli.py`.

```bash
./terrain_cli.py [OPTIONS] COMMAND [ARGS]...
```

**Common Options:**

*   `-h`, `--help`: Show help message and exit.
*   `--version`: Show version information and exit.

---

### `download` Command

Downloads tiles for a specified region and zoom levels.

**Syntax:**

```bash
./terrain_cli.py download [OPTIONS] -- <min_lon,min_lat,max_lon,max_lat>
```
*   **Important:** The `--` before the BBOX is **required** if `min_lon` is negative to prevent it from being interpreted as an option.

**Arguments:**

*   `<min_lon,min_lat,max_lon,max_lat>`: The bounding box coordinates (WGS84).

**Options:**

*   `-z, --zoom-range <min,max>`: Zoom levels (e.g., `10,14`). Default: `10,15`. Max: `15`.
*   `-o, --output-dir <dir>`: Directory to save tiles. Default: `terrain_tiles`.
*   `-c, --concurrency <int>`: Number of download workers. Default: `10`.
*   `--only-missing`: Only download missing tiles. Checks existence first.
*   `-y, --yes`: Skip confirmation prompt before downloading.

**Examples:**

```bash
# Download zoom levels 10-14 for Los Angeles area
# Note the '--' because the minimum longitude is negative
./terrain_cli.py download -z 10,14 -- -118.67,33.70,-118.15,34.34

# Download only missing tiles for zoom 12 with 20 workers, skip prompt
./terrain_cli.py download -z 12,12 -c 20 --only-missing -y -- -118.67,33.70,-118.15,34.34

# Download zoom 10-13 to a specific directory 'la_tiles'
./terrain_cli.py download -z 10,13 -o ./la_tiles -- -118.67,33.70,-118.15,34.34
```

---

### `check` Command

Verifies existing tiles within a bounding box for completeness and basic integrity (file existence and 256x256 dimensions).

**Syntax:**

```bash
./terrain_cli.py check [OPTIONS] -- <min_lon,min_lat,max_lon,max_lat>
```
*   **Important:** The `--` before the BBOX is **required** if `min_lon` is negative.

**Arguments:**

*   `<min_lon,min_lat,max_lon,max_lat>`: The bounding box coordinates to check against.

**Options:**

*   `-z, --zoom-range <min,max>`: Zoom levels to check (e.g., `10,14`). Default: `10,15`. Max: `15`.
*   `-o, --output-dir <dir>`: Directory containing the tiles to check. Default: `terrain_tiles`.

**Examples:**

```bash
# Check zoom levels 10-14 for Los Angeles in the default 'terrain_tiles' directory
./terrain_cli.py check -z 10,14 -- -118.67,33.70,-118.15,34.34

# Check only zoom level 12 in a specific directory 'la_tiles'
./terrain_cli.py check -z 12,12 -o ./la_tiles -- -118.67,33.70,-118.15,34.34
```

---

## Configuration (`tiles.json`)

After a successful download operation that fetches or confirms the existence of tiles, the tool generates a `tiles.json` file in the root of the specified output directory (e.g., `terrain_tiles/tiles.json`). This file follows the [TileJSON specification](https://github.com/mapbox/tilejson-spec) and describes the tile set, making it easy to integrate with mapping libraries. It includes metadata such as:

*   Tile URL pattern (relative path: `{z}/{x}/{y}.png`)
*   Bounding box of the downloaded region
*   Estimated center point
*   Min/Max zoom levels downloaded
*   Data attribution
*   Encoding type (`terrarium`)

## File Structure

```
AWS-Dem-Downloader/
├── terrain_cli.py        # Main command-line interface script
├── terrain_utils.py      # Core logic for tile checking and downloading
├── requirements.txt      # Python dependencies
├── terrain_tiles/        # Default output directory for tiles & reports
│   ├── 0/                # Zoom level directories (if downloaded)
│   ├── ...               # ...
│   ├── 15/
│   │   ├── x_coord/      # Tile X coordinate directory
│   │   │   └── y_coord.png # Tile image (Y coordinate filename)
│   │   └── ...
│   ├── tiles.json        # TileJSON metadata file (generated after download)
│   ├── download_report.json # Report from the last download operation
│   └── check_report.json    # Report from the last check operation
├── public/
│   └── terrarium.png     # Project image (referenced in README)
└── README.md             # This file
```

## Tile Format

The downloaded tiles are PNG images encoded using the [Terrarium specification](https://github.com/tilezen/joerd/blob/master/docs/formats.md#terrarium-10). Elevation `h` (in meters) is calculated from the Red (R), Green (G), and Blue (B) channel values (0-255) as:

`h = (R * 256 + G + B / 256) - 32768`

## License

This project is licensed under the MIT License - see the `LICENSE` file (you may need to create one) for details.

## Acknowledgements

*   Tile data sourced from the [AWS Open Data Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) dataset.
*   Based on the Terrarium tile specification used by Mapzen/Tilezen.
```

**Remember to:**

1.  Place your `terrarium.png` image inside a `public` folder within your project directory.
2.  Replace `<your-repository-url>` in the "Installation" section if applicable.
3.  Consider adding a `LICENSE` file (e.g., containing the MIT License text) if you don't have one.