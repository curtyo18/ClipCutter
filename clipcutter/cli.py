"""Click CLI: process and review commands."""

from pathlib import Path

import click

from clipcutter import __version__


@click.group()
@click.version_option(version=__version__)
def cli():
    """ClipCutter: Audio-based video highlight extractor."""
    pass


@cli.command()
@click.argument("input_path", type=click.Path(exists=True))
@click.option("-o", "--output", "output_dir", type=click.Path(),
              default="./output", show_default=True,
              help="Output directory for clips and metadata.")
@click.option("-s", "--sensitivity", type=click.FloatRange(min=0.1, max=5.0),
              default=1.0, show_default=True,
              help="Detection sensitivity (0.1-5.0). Higher = more clips.")
@click.option("--recursive", is_flag=True, default=False,
              help="Process video files in subdirectories.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Analyze and report highlights without extracting clips.")
@click.option("--context", type=click.FloatRange(min=0), default=None,
              help="Seconds of context before/after each highlight (default: 20).")
@click.option("--overwrite", is_flag=True, default=False,
              help="Overwrite existing clips without prompting.")
def process(input_path, output_dir, sensitivity, recursive, dry_run, context, overwrite):
    """Analyze video files and extract highlight clips.

    INPUT_PATH can be a single video file or a directory of videos.
    """
    from clipcutter import config
    from clipcutter.pipeline import process_video, process_directory

    input_path = Path(input_path)
    output_dir = Path(output_dir)

    # Override context padding if specified
    if context is not None:
        config.CLIP_CONTEXT_BEFORE_SECONDS = context
        config.CLIP_CONTEXT_AFTER_SECONDS = context

    click.echo(f"ClipCutter v{__version__}")
    click.echo(f"Output: {output_dir.resolve()}")
    click.echo(f"Sensitivity: {sensitivity}")
    if context is not None:
        click.echo(f"Context: {context}s before/after")
    if dry_run:
        click.echo("Mode: DRY RUN (no clips will be extracted)")
    click.echo()

    if input_path.is_file():
        process_video(input_path, output_dir, sensitivity, dry_run, overwrite)
    elif input_path.is_dir():
        process_directory(input_path, output_dir, sensitivity, recursive, dry_run, overwrite)
    else:
        click.echo(f"Error: {input_path} is not a file or directory.")
        raise SystemExit(1)


@cli.command()
@click.option("-o", "--output", "output_dir", type=click.Path(exists=True),
              default="./output", show_default=True,
              help="Output directory containing clips.")
@click.option("--player", type=str, default="auto", show_default=True,
              help="Video player command (e.g., 'vlc', 'mpv'). Default: system default.")
@click.option("--source", type=str, default=None,
              help="Only review clips from this source video.")
def review(output_dir, player, source):
    """Interactively review pending clips in the terminal: keep or discard."""
    from clipcutter.reviewer import review_clips

    review_clips(Path(output_dir), player, source)


@cli.command()
@click.option("-o", "--output", "output_dir", type=click.Path(),
              default="./output", show_default=True,
              help="Output directory for clips and metadata.")
@click.option("-p", "--port", type=int, default=8000, show_default=True,
              help="Port to run the web UI on.")
def ui(output_dir, port):
    """Open the browser-based review UI."""
    import asyncio
    import sys
    import webbrowser
    import uvicorn
    from clipcutter.web import create_app

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(output_dir)

    click.echo(f"ClipCutter Review UI")
    click.echo(f"Clips from: {output_dir.resolve()}")
    click.echo(f"Opening http://localhost:{port}")
    click.echo("Press Ctrl+C to stop.\n")

    webbrowser.open(f"http://localhost:{port}")

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # Suppress noisy asyncio socket errors on Windows
    import logging
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
