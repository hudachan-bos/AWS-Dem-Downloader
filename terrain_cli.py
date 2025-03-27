#!/usr/bin/env python3
import click
import os
import json
import logging
from datetime import datetime
from typing import Tuple, List, Set, Dict, Optional

# Assuming terrain_utils.py is in the same directory or Python path
from terrain_utils import (
    TileManager,
    calculate_size_estimate,
    DEFAULT_CONCURRENCY,
)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

# --- CLI Helper Functions ---

def parse_bbox(ctx, param, value: str) -> Tuple[float, float, float, float]:
    """Parse bbox string: min_lon,min_lat,max_lon,max_lat"""
    try:
        # Standard order: min_lon, min_lat, max_lon, max_lat
        min_lon, min_lat, max_lon, max_lat = map(float, value.split(","))
        if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180 and
                -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            raise ValueError("Coordinates out of valid range.")
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ValueError("Min coordinates must be less than max coordinates.")
        # Return order expected by TileManager: min_lat, min_lon, max_lat, max_lon
        return min_lat, min_lon, max_lat, max_lon
    except ValueError as e:
        raise click.BadParameter(
            f"Bbox must be 4 comma-separated floats in order: "
            f"min_lon,min_lat,max_lon,max_lat. Error: {e}"
        )

def validate_zoom_range(ctx, param, value: str) -> range:
    """Parse zoom range string: min_zoom,max_zoom"""
    try:
        min_zoom, max_zoom = map(int, value.split(","))
        # AWS Terrain Tiles typically go up to zoom 15
        if not (0 <= min_zoom <= 15 and 0 <= max_zoom <= 15):
             raise ValueError("Zoom levels must be between 0 and 15.")
        if min_zoom > max_zoom:
            raise ValueError("Min zoom cannot be greater than max zoom.")
        # range() excludes the stop value, so add 1
        return range(min_zoom, max_zoom + 1)
    except ValueError as e:
        raise click.BadParameter(
            f"Zoom range must be in format: min_zoom,max_zoom (e.g., 10,15). Error: {e}"
        )

def print_summary(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    zoom_range: range,
    tiles_per_zoom: Dict[int, int],
    title: str,
):
    """Prints a summary of the operation."""
    total_tiles = sum(tiles_per_zoom.values())
    total_size, total_unit = calculate_size_estimate(total_tiles)

    click.echo(f"\n--- {title} ---")
    click.echo(f"Bounding Box:")
    # Display in standard lon/lat order for user clarity
    click.echo(f"  SW Corner (lon, lat): {min_lon:.6f}, {min_lat:.6f}")
    click.echo(f"  NE Corner (lon, lat): {max_lon:.6f}, {max_lat:.6f}")
    click.echo(f"Zoom Levels: {zoom_range.start} to {zoom_range.stop - 1}")
    click.echo(f"Estimated Total Tiles: {total_tiles:,}")
    click.echo(f"Estimated Total Size: {total_size:.2f} {total_unit}")

    click.echo("\nBreakdown by Zoom Level:")
    for zoom in zoom_range:
        count = tiles_per_zoom.get(zoom, 0)
        if count > 0:
            size, unit = calculate_size_estimate(count)
            click.echo(f"  Zoom {zoom}: {count:,} tiles (~{size:.2f} {unit})")
    click.echo("-" * (len(title) + 6))


# --- JSON Serialization Helper ---
def set_serializer(obj):
    """Convert sets to lists for JSON serialization."""
    if isinstance(obj, set):
        # Convert tuples (like tile coords) to strings "x_y" for readability
        return sorted([f"{item[0]}_{item[1]}" for item in obj])
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


# --- Click CLI Group ---
@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version="1.2.1") # Incremented version
def cli():
    """
    Terrain Tiles Download and Verification Tool (v1.2.1)

    Downloads Terrarium PNG terrain tiles from AWS Open Data for a given
    bounding box and zoom range, with support for concurrent downloads.
    Also provides tools to check integrity of existing tile sets.

    BBOX Format: min_lon,min_lat,max_lon,max_lat

    \b
    IMPORTANT: If your BBOX coordinates start with a hyphen (e.g., negative
    longitude), you MUST place '--' before the BBOX argument to prevent it
    from being interpreted as an option.

    \b
    Common Options:
      -h, --help     Show this help message and exit.
      --version      Show the version and exit.

    \b
    Commands:
      download  Download terrain tiles for a bounding box.
      check     Check existing tiles for completeness and basic integrity.

    \b
    Example Usage:
    \b
      Download tiles for Los Angeles (zoom 10-14), using '--':
        ./terrain_cli.py download -z 10,14 -- -118.67,33.70,-118.15,34.34
    \b
      Check existing tiles in 'la_tiles' directory (zoom 10-14), using '--':
        ./terrain_cli.py check -z 10,14 -o la_tiles -- -118.67,33.70,-118.15,34.34
    \b
      Download only missing tiles with 20 workers, skipping confirmation:
        ./terrain_cli.py download -z 10,14 -c 20 --only-missing -y -- -118.67,33.70,-118.15,34.34
    """
    # Setup logging level (can be adjusted based on verbosity flags later)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    # Suppress overly verbose logs from libraries if needed
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# --- Download Command ---
@cli.command()
# Options must come before arguments in the decorator list for help generation
@click.option(
    "--zoom-range",
    "-z",
    default="10,15",
    callback=validate_zoom_range,
    help="Zoom range: min_zoom,max_zoom (e.g., 10,15). Max 15.",
    show_default=True,
)
@click.option(
    "--output-dir",
    "-o",
    default="terrain_tiles",
    type=click.Path(file_okay=False, dir_okay=True, writable=True, resolve_path=True),
    help="Output directory for tiles.",
    show_default=True,
)
@click.option(
    "--concurrency",
    "-c",
    type=int,
    default=DEFAULT_CONCURRENCY,
    help="Number of concurrent download workers.",
    show_default=True,
)
@click.option(
    "--only-missing",
    is_flag=True,
    default=False,
    help="Only download tiles that are missing (checks existence before download).",
    show_default=True,
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt before downloading.",
    show_default=True,
)
@click.argument("bbox", metavar="<min_lon,min_lat,max_lon,max_lat>", callback=parse_bbox)
def download(
    zoom_range: range, # Argument order matches decorators now
    output_dir: str,
    concurrency: int,
    only_missing: bool,
    yes: bool,
    bbox: Tuple[float, float, float, float],
):
    """
    Download terrain tiles for a bounding box.

    \b
    Arguments:
      <bbox>  Bounding box coordinates in format: min_lon,min_lat,max_lon,max_lat
              Example: -118.67,33.70,-118.15,34.34 (Los Angeles area)
              IMPORTANT: If BBOX starts with '-', place '--' before it on the
              command line (e.g., ... -- -118.67,33.70,...).

    \b
    Options:
      -z, --zoom-range <min,max>  Zoom range (default: 10,15). Max 15.
      -o, --output-dir <dir>      Output directory (default: terrain_tiles).
      -c, --concurrency <int>     Number of download workers (default: 10).
      --only-missing              Only download missing tiles. Checks existence first.
      -y, --yes                   Skip confirmation prompt.
      -h, --help                  Show this message and exit.

    \b
    Resolution Guide (approximate):
      Zoom 10: ~153 meters/pixel
      Zoom 12: ~38 meters/pixel
      Zoom 14: ~9.5 meters/pixel
      Zoom 15: ~4.8 meters/pixel

    \b
    Examples:
      # Download tiles for Los Angeles region (zoom 10-14), using '--':
      ./terrain_cli.py download -z 10,14 -- -118.67,33.70,-118.15,34.34

      # Download only missing tiles, skip confirmation, use 16 workers:
      ./terrain_cli.py download -z 10,14 --only-missing -y -c 16 -- -118.67,33.70,-118.15,34.34

      # Specify custom output directory:
      ./terrain_cli.py download -z 10,14 -o ./la_terrain_tiles -- -118.67,33.70,-118.15,34.34
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    # Ensure output directory exists before TileManager tries to use it
    os.makedirs(output_dir, exist_ok=True)
    manager = TileManager(output_dir)

    click.echo(f"Output directory: {output_dir}") # Already resolved path
    if concurrency <= 0:
        click.echo("Concurrency must be positive. Using default.", err=True)
        concurrency = DEFAULT_CONCURRENCY

    # --- Calculate expected tiles ---
    tiles_per_zoom: Dict[int, int] = {}
    expected_tiles_all_zooms: Dict[int, Set[Tuple[int, int]]] = {}
    click.echo("Calculating expected tiles...")
    for zoom in zoom_range:
        expected = manager.get_expected_tiles(
            min_lat, min_lon, max_lat, max_lon, zoom
        )
        expected_tiles_all_zooms[zoom] = expected
        tiles_per_zoom[zoom] = len(expected)

    total_expected_tiles = sum(tiles_per_zoom.values())
    if total_expected_tiles == 0:
        click.echo("Bounding box and zoom range resulted in 0 expected tiles.")
        return

    # --- Determine tiles to download ---
    tiles_to_download: Optional[Dict[int, Set[Tuple[int, int]]]] = None
    title = "Download Plan"
    target_tile_count = total_expected_tiles

    if only_missing:
        click.echo("Checking for missing tiles...")
        missing_tiles = manager.get_missing_tiles(
            min_lat, min_lon, max_lat, max_lon, zoom_range
        )
        tiles_to_download = missing_tiles # Target only the missing ones
        # Update tiles_per_zoom to reflect only missing counts for the summary
        tiles_per_zoom_missing = {z: len(t) for z, t in missing_tiles.items() if t}
        target_tile_count = sum(tiles_per_zoom_missing.values())
        title = "Download Plan (Missing Tiles Only)"
        if target_tile_count == 0:
            click.echo("\nAll expected tiles already exist. Nothing to download.")
            click.echo("Use the 'check' command for corruption checks, or run 'download' without '--only-missing' to overwrite.")
            return
        # Use the missing counts for the summary if in this mode
        print_summary(min_lat, min_lon, max_lat, max_lon, zoom_range, tiles_per_zoom_missing, title)
    else:
        # Download all expected tiles (will overwrite existing)
        tiles_to_download = expected_tiles_all_zooms
        title = "Download Plan (Will Overwrite Existing)"
        # Use the total counts for the summary
        print_summary(min_lat, min_lon, max_lat, max_lon, zoom_range, tiles_per_zoom, title)


    click.echo(f"Using {concurrency} concurrent workers.")
    click.echo("Warning: SSL verification is disabled for downloads!", err=True)

    # --- Ask for Confirmation ---
    if not yes:
        # Use click.confirm which handles the y/n prompt and aborts if 'n'
        click.confirm("\nProceed with download?", abort=True, default=False)

    # --- Execute Download ---
    start_time = datetime.now()
    result = manager.download_tiles(
        min_lat,
        min_lon,
        max_lat,
        max_lon,
        zoom_range,
        tiles_to_download=tiles_to_download, # Pass the specific set to download
        concurrency=concurrency,
    )
    end_time = datetime.now()
    duration = end_time - start_time

    # --- Save Report ---
    report = {
        "timestamp": start_time.isoformat(),
        "duration_seconds": duration.total_seconds(),
        "bbox_input": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "zoom_range_input": f"{zoom_range.start},{zoom_range.stop - 1}",
        "output_directory": output_dir,
        "concurrency": concurrency,
        "only_missing_mode": only_missing,
        "total_tiles_in_plan": target_tile_count,
        "downloaded_count": len(result["downloaded"]),
        "failed_count": len(result["failed"]),
        "skipped_count": len(result["skipped"]),
    }
    if result["failed"]: # Add failed list if not empty
        report["failed_tiles"] = sorted(result["failed"]) # Sort for consistency

    report_file = os.path.join(output_dir, "download_report.json")
    try:
        with open(report_file, "w") as f:
            # Use default=str for any types json doesn't handle directly (like sets if serializer missed)
            json.dump(report, f, indent=2, default=str)
        click.echo(f"\nDownload report saved to: {report_file}")
    except IOError as e:
        click.echo(f"\nError saving download report: {e}", err=True)

    # --- Print Final Summary ---
    click.echo("\n--- Download Complete ---")
    click.echo(f"Duration: {duration}")
    click.echo(f"Downloaded: {len(result['downloaded']):,} tiles")
    click.echo(f"Failed: {len(result['failed']):,} tiles")
    click.echo(f"Skipped (already existed): {len(result['skipped']):,} tiles")
    if result["failed"]:
        click.echo(f"Check report file ({report_file}) for list of failed tiles.", err=True)


# --- Check Command ---
@cli.command()
# Options must come before arguments
@click.option(
    "--zoom-range",
    "-z",
    default="10,15",
    callback=validate_zoom_range,
    help="Zoom range: min_zoom,max_zoom (e.g., 10,15). Max 15.",
    show_default=True,
)
@click.option(
    "--output-dir",
    "-o",
    default="terrain_tiles",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True, resolve_path=True),
    help="Directory containing the tiles to check.",
    show_default=True,
)
@click.argument("bbox", metavar="<min_lon,min_lat,max_lon,max_lat>", callback=parse_bbox)
def check(
    zoom_range: range, # Argument order matches decorators
    output_dir: str,
    bbox: Tuple[float, float, float, float],
):
    """
    Check existing tiles for completeness and basic integrity.

    Verifies tile existence and dimensions (256x256) within the BBOX.
    Note: Checking dimensions requires opening each image file, which can be slow.

    \b
    Arguments:
      <bbox>  Bounding box coordinates in format: min_lon,min_lat,max_lon,max_lat
              Example: -118.67,33.70,-118.15,34.34 (Los Angeles area)
              IMPORTANT: If BBOX starts with '-', place '--' before it on the
              command line (e.g., ... -- -118.67,33.70,...).

    \b
    Options:
      -z, --zoom-range <min,max>  Zoom range to check (default: 10,15). Max 15.
      -o, --output-dir <dir>      Tiles directory to check (default: terrain_tiles).
      -h, --help                  Show this message and exit.

    \b
    The check command will:
      1. Calculate all expected tiles for the BBOX and zoom levels.
      2. Verify if each expected tile file exists.
      3. Attempt to open existing tiles and check if dimensions are 256x256.
      4. Report counts of existing, missing, and corrupt tiles.
      5. Save a detailed JSON report ('check_report.json').

    \b
    Examples:
      # Check tiles for Los Angeles region (zoom 10-14), using '--':
      ./terrain_cli.py check -z 10,14 -- -118.67,33.70,-118.15,34.34

      # Check specific zoom levels in a custom directory:
      ./terrain_cli.py check -z 12,12 -o ./la_terrain_tiles -- -118.67,33.70,-118.15,34.34
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    manager = TileManager(output_dir)

    click.echo(f"Checking tiles in directory: {output_dir}") # Already resolved path
    click.echo("Note: Corruption check involves opening image files and may be slow.")

    # --- Calculate expected tiles for summary ---
    tiles_per_zoom: Dict[int, int] = {}
    click.echo("Calculating expected tiles...")
    for zoom in zoom_range:
        expected = manager.get_expected_tiles(
            min_lat, min_lon, max_lat, max_lon, zoom
        )
        tiles_per_zoom[zoom] = len(expected)

    print_summary(min_lat, min_lon, max_lat, max_lon, zoom_range, tiles_per_zoom, "Tile Check Plan")

    # --- Perform Check ---
    start_time = datetime.now()
    result = manager.check_tiles(min_lat, min_lon, max_lat, max_lon, zoom_range)
    end_time = datetime.now()
    duration = end_time - start_time

    # --- Prepare and Save Report ---
    total_existing = sum(len(t) for t in result["existing"].values())
    total_missing = sum(len(t) for t in result["missing"].values())
    total_corrupt = sum(len(t) for t in result["corrupt"].values())
    total_expected_check = total_existing + total_missing + total_corrupt # Should match calculation above

    report = {
        "timestamp": start_time.isoformat(),
        "duration_seconds": duration.total_seconds(),
        "bbox_input": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "zoom_range_input": f"{zoom_range.start},{zoom_range.stop - 1}",
        "checked_directory": output_dir,
        "total_expected": total_expected_check,
        "total_existing_valid_size": total_existing,
        "total_missing": total_missing,
        "total_corrupt_or_unreadable": total_corrupt,
        "details_by_zoom": {},
    }
    # Add lists of missing/corrupt if they are not empty and use serializer
    if total_missing > 0:
        report["missing_tiles_by_zoom"] = result["missing"]
    if total_corrupt > 0:
        report["corrupt_tiles_by_zoom"] = result["corrupt"]


    for zoom in zoom_range:
        report["details_by_zoom"][zoom] = {
            "expected": tiles_per_zoom.get(zoom, 0),
            "existing": len(result["existing"].get(zoom, set())),
            "missing": len(result["missing"].get(zoom, set())),
            "corrupt": len(result["corrupt"].get(zoom, set())),
        }

    report_file = os.path.join(output_dir, "check_report.json")
    try:
        with open(report_file, "w") as f:
            # Use the custom serializer for sets (converts tile tuples to "x_y" strings)
            json.dump(report, f, indent=2, default=set_serializer)
        click.echo(f"\nCheck report saved to: {report_file}")
    except IOError as e:
        click.echo(f"\nError saving check report: {e}", err=True)

    # --- Print Summary Results ---
    click.echo("\n--- Check Complete ---")
    click.echo(f"Duration: {duration}")
    click.echo(f"Total Expected: {total_expected_check:,}")
    click.echo(f"Total Existing (and valid size): {total_existing:,}")
    click.echo(f"Total Missing: {total_missing:,}")
    click.echo(f"Total Corrupt (unreadable or wrong size): {total_corrupt:,}")

    if total_missing > 0 or total_corrupt > 0:
        click.echo(f"Issues found. Check report file ({report_file}) for details.", err=True)
        if total_missing > 0:
            click.echo("Consider running 'download --only-missing' to fetch missing tiles.")


if __name__ == "__main__":
    cli()
