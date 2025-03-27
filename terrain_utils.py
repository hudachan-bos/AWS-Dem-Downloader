import os
import math
import json
import logging
from PIL import Image
import requests
import urllib3
from typing import Dict, Set, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# --- Constants ---
BASE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
DEFAULT_TILE_SIZE_BYTES = 100 * 1024  # Approx. 100KB per tile
DEFAULT_CONCURRENCY = 10
TILE_DIMENSION = 256  # Expected dimension for valid tiles

# --- Setup ---
# Disable SSL verification warnings (Use with caution)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# Configure basic logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


# --- Helper Functions ---
def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> Tuple[int, int]:
    """Convert latitude/longitude to tile coordinates (slippy map tilenames)"""
    lat_rad = math.radians(lat_deg)
    n = 2.0**zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)


def num2deg(xtile: int, ytile: int, zoom: int) -> Tuple[float, float]:
    """Convert tile coordinates to latitude/longitude (north-west corner)"""
    n = 2.0**zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return (lat_deg, lon_deg)


def calculate_size_estimate(tile_count: int) -> Tuple[float, str]:
    """Calculate estimated size in MB/GB"""
    size_bytes = tile_count * DEFAULT_TILE_SIZE_BYTES
    if size_bytes >= 1024 * 1024 * 1024:  # Use GB
        return size_bytes / (1024 * 1024 * 1024), "GB"
    else:  # Use MB
        return size_bytes / (1024 * 1024), "MB"


# --- Core Classes ---
class TileManager:
    """Manages tile checking and downloading operations."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)


    def get_expected_tiles(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        zoom: int,
    ) -> Set[Tuple[int, int]]:
        """
        Calculate expected tiles for a zoom level.
        Returns a set of (x, y) tuples.
        """
        # Calculate tile coordinates for the corners
        # Note: Higher latitude gives LOWER y tile index
        x_for_minlon, y_for_maxlat = deg2num(max_lat, min_lon, zoom)
        x_for_maxlon, y_for_minlat = deg2num(min_lat, max_lon, zoom)

        # Determine the actual min/max tile indices for looping
        min_x = x_for_minlon
        max_x = x_for_maxlon
        min_y = y_for_maxlat  # y corresponding to max latitude is the min y index
        max_y = y_for_minlat  # y corresponding to min latitude is the max y index

        # Ensure x coordinates are in the correct order (can happen near antimeridian)
        if min_x > max_x:
             # Handle crossing the antimeridian if necessary, though less common for smaller regions
             # For simplicity here, we'll just swap if the input bbox wasn't strictly SW to NE
             # A more robust solution might involve checking longitude ranges carefully.
             logging.warning("min_x > max_x detected, potentially incorrect BBOX order or antimeridian crossing. Swapping x bounds.")
             min_x, max_x = max_x, min_x

        # Ensure y coordinates are in the correct order (should always be min_y <= max_y here)
        if min_y > max_y:
            # This case *shouldn't* happen if latitudes are correct, but good to check.
             logging.error(f"Calculated min_y ({min_y}) > max_y ({max_y}). Check latitude inputs.")
             # Swap them to prevent empty range, though the result might be wrong
             min_y, max_y = max_y, min_y


        # Generate tile coordinates using the correct numeric ranges
        return {
            (x, y)
            for x in range(min_x, max_x + 1)
            for y in range(min_y, max_y + 1) # Loop from the numerically smaller y to the larger y
        }


    def get_tile_bounds_info(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        zoom: int,
    ) -> Dict:
        """Get tile boundary information for a given bbox and zoom level."""
        min_x, max_y = deg2num(max_lat, min_lon, zoom)
        max_x, min_y = deg2num(min_lat, max_lon, zoom)

        width = max_x - min_x + 1
        height = max_y - min_y + 1
        total_tiles = width * height

        return {
            "zoom": zoom,
            "bounds": {
                "min_x": min_x,
                "max_x": max_x,
                "min_y": min_y,
                "max_y": max_y,
            },
            "dimensions": {"width": width, "height": height},
            "total_tiles": total_tiles,
        }

    def check_tiles(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        zoom_levels: range,
    ) -> Dict[str, Dict[int, Set[Tuple[int, int]]]]:
        """
        Check existing tiles, identify missing and corrupt ones.
        Note: Corruption check involves opening images, which can be slow.
        """
        results: Dict[str, Dict[int, Set[Tuple[int, int]]]] = {
            "missing": {},
            "corrupt": {},
            "existing": {},
        }

        for zoom in zoom_levels:
            zoom_dir = os.path.join(self.base_dir, str(zoom))
            expected_coords = self.get_expected_tiles(
                min_lat, min_lon, max_lat, max_lon, zoom
            )

            results["missing"][zoom] = set()
            results["corrupt"][zoom] = set()
            results["existing"][zoom] = set()

            if not os.path.exists(zoom_dir):
                results["missing"][zoom] = expected_coords
                logging.warning(f"Zoom directory not found: {zoom_dir}")
                continue

            logging.info(
                f"Checking zoom {zoom} ({len(expected_coords)} tiles)..."
            )
            for x, y in tqdm(expected_coords, desc=f"Checking Z{zoom}"):
                tile_path = os.path.join(zoom_dir, str(x), f"{y}.png")

                if not os.path.exists(tile_path):
                    results["missing"][zoom].add((x, y))
                    continue

                try:
                    with Image.open(tile_path) as img:
                        # Basic check: verify dimensions
                        if img.size != (TILE_DIMENSION, TILE_DIMENSION):
                            results["corrupt"][zoom].add((x, y))
                            logging.warning(
                                f"Corrupt tile (wrong size {img.size}): {tile_path}"
                            )
                        else:
                            # Could add more checks here (e.g., format, mode) if needed
                            results["existing"][zoom].add((x, y))
                except Exception as e:
                    results["corrupt"][zoom].add((x, y))
                    logging.error(f"Corrupt tile (cannot open): {tile_path} - {e}")

        return results

    def get_missing_tiles(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        zoom_levels: range,
    ) -> Dict[int, Set[Tuple[int, int]]]:
        """Get only missing tiles by checking file existence (faster)."""
        missing_tiles: Dict[int, Set[Tuple[int, int]]] = {}

        for zoom in zoom_levels:
            zoom_dir = os.path.join(self.base_dir, str(zoom))
            expected_coords = self.get_expected_tiles(
                min_lat, min_lon, max_lat, max_lon, zoom
            )
            missing_tiles[zoom] = set()

            if not os.path.exists(zoom_dir):
                missing_tiles[zoom] = expected_coords
                continue

            logging.info(
                f"Scanning for missing tiles in zoom {zoom}..."
            )
            for x, y in expected_coords:
                tile_path = os.path.join(zoom_dir, str(x), f"{y}.png")
                if not os.path.exists(tile_path):
                    missing_tiles[zoom].add((x, y))

        return missing_tiles

    def _download_single_tile(
        self, zoom: int, x: int, y: int, session: requests.Session
    ) -> Tuple[str, Optional[str]]:
        """Downloads a single tile, returns status and tile identifier."""
        tile_id = f"{zoom}/{x}/{y}"
        output_dir = os.path.join(self.base_dir, str(zoom), str(x))
        output_file = os.path.join(output_dir, f"{y}.png")
        url = BASE_URL.format(z=zoom, x=x, y=y)

        # Skip if already exists (should ideally be handled by caller, but double-check)
        if os.path.exists(output_file):
            return "skipped", tile_id

        os.makedirs(output_dir, exist_ok=True)

        try:
            # Use the session for potential connection pooling
            response = session.get(url, verify=False, timeout=30)
            response.raise_for_status()  # Raises HTTPError for bad responses (4xx or 5xx)

            with open(output_file, "wb") as f:
                f.write(response.content)

            # Optional: Add a verification step here (e.g., check size, open with PIL)
            # try:
            #     with Image.open(output_file) as img:
            #         if img.size != (TILE_DIMENSION, TILE_DIMENSION):
            #             os.remove(output_file) # Clean up invalid download
            #             return "failed", f"{tile_id} (Invalid Size)"
            # except Exception:
            #     os.remove(output_file) # Clean up invalid download
            #     return "failed", f"{tile_id} (Cannot Verify)"

            return "downloaded", tile_id

        except requests.exceptions.RequestException as e:
            logging.error(f"Failed downloading {tile_id}: {e}")
            # Clean up potentially incomplete file
            if os.path.exists(output_file):
                try:
                    os.remove(output_file)
                except OSError:
                    pass # Ignore error if file couldn't be removed
            return "failed", tile_id
        except Exception as e:
            logging.error(f"Unexpected error for {tile_id}: {e}")
            if os.path.exists(output_file):
                try:
                    os.remove(output_file)
                except OSError:
                    pass
            return "failed", tile_id

    def download_tiles(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        zoom_levels: range,
        tiles_to_download: Optional[Dict[int, Set[Tuple[int, int]]]] = None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> Dict[str, List[str]]:
        """
        Download tiles concurrently.
        If `tiles_to_download` is provided, only downloads those.
        Otherwise, calculates expected tiles and downloads all.
        """
        download_results: Dict[str, List[str]] = {
            "downloaded": [],
            "failed": [],
            "skipped": [],
        }
        tasks = []

        if tiles_to_download is None:
            # If specific tiles aren't provided, calculate all expected tiles
            tiles_to_download = {}
            logging.info("Calculating all expected tiles for download...")
            for zoom in zoom_levels:
                tiles_to_download[zoom] = self.get_expected_tiles(
                    min_lat, min_lon, max_lat, max_lon, zoom
                )

        # Prepare list of download tasks
        for zoom in zoom_levels:
            if zoom in tiles_to_download:
                for x, y in tiles_to_download[zoom]:
                    tasks.append((zoom, x, y))

        if not tasks:
            logging.info("No tiles scheduled for download.")
            return download_results

        logging.info(
            f"Starting download of {len(tasks)} tiles with {concurrency} workers..."
        )

        # Use a session object for potential performance benefits
        with requests.Session() as session:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                # Submit tasks
                futures = {
                    executor.submit(self._download_single_tile, z, x, y, session): (z, x, y)
                    for z, x, y in tasks
                }

                # Process completed tasks with progress bar
                for future in tqdm(
                    as_completed(futures),
                    total=len(tasks),
                    desc="Downloading Tiles",
                ):
                    try:
                        status, tile_id = future.result()
                        if tile_id: # Ensure tile_id is not None
                            download_results[status].append(tile_id)
                    except Exception as e:
                        # Log exceptions from the future itself (should be rare if _download_single_tile handles errors)
                        z, x, y = futures[future]
                        tile_id = f"{z}/{x}/{y}"
                        logging.error(f"Error processing future for {tile_id}: {e}")
                        download_results["failed"].append(f"{tile_id} (Future Error)")

        # Generate tiles.json after download completes
        if download_results["downloaded"] or download_results["skipped"]:
            try:
                self._generate_tiles_json(
                    min_lat, min_lon, max_lat, max_lon, zoom_levels
                )
            except Exception as e:
                logging.error(f"Failed to generate tiles.json: {e}")
        else:
            logging.warning(
                "No tiles were downloaded or skipped. Skipping tiles.json generation."
            )

        return download_results

    def _generate_tiles_json(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        zoom_levels: range,
    ) -> str:
        """Generate tiles.json metadata file."""
        logging.info("Generating tiles.json configuration...")

        center_lon = round((min_lon + max_lon) / 2, 6)
        center_lat = round((min_lat + max_lat) / 2, 6)
        # Sensible default center zoom - middle of the requested range
        center_zoom = (zoom_levels.start + zoom_levels.stop - 1) // 2

        # Use relative path from tiles.json location
        tile_path_pattern = "{z}/{x}/{y}.png"

        config = {
            "tilejson": "2.2.0", # Use a common version
            "name": f"Terrain Tiles {os.path.basename(self.base_dir)}",
            "description": f"Terrarium encoded terrain tiles for the specified region ({self.base_dir})",
            "version": "1.0.0",
            "attribution": "Mapzen, OpenStreetMap, AWS Terrain Tiles", # Standard attribution
            "scheme": "xyz",
            "tiles": [tile_path_pattern], # Relative path
            "minzoom": zoom_levels.start,
            "maxzoom": zoom_levels.stop - 1,
            "bounds": [
                round(min_lon, 6),
                round(min_lat, 6),
                round(max_lon, 6),
                round(max_lat, 6),
            ],
            "center": [center_lon, center_lat, center_zoom],
            "format": "png",
            "encoding": "terrarium", # Specify encoding
        }

        config_path = os.path.join(self.base_dir, "tiles.json")
        try:
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            logging.info(f"Successfully created tiles.json at {config_path}")
            # print(json.dumps(config, indent=2)) # Optional: print config
        except IOError as e:
            logging.error(f"Error writing tiles.json to {config_path}: {e}")
            raise # Re-raise the exception

        return config_path

