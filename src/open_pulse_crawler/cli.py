"""Command-line interface for the GitHub crawler."""

import sys
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from dotenv import load_dotenv

from .models import GraphData
from .platforms.github import GitHubClient, resolve_cache_dir
from .crawler import GitHubCrawler
from .io_utils import parse_seed_file, export_to_json, export_to_csv, export_nodes_csv
from .token_env import POOL_ENV, TOKEN_ENV, resolve_github_tokens, tokens_not_set_message
from .visualization import visualize_graph, visualize_clusters as viz_clusters, VISUALIZATION_AVAILABLE

app = typer.Typer(help="GitHub BFS Crawler - Discover GitHub users, organizations, and repositories")
console = Console()
logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            RichHandler(console=console, rich_tracebacks=True, show_time=False)
        ]
    )


def get_github_tokens() -> List[str]:
    """Get GitHub tokens from environment (or `.env` in cwd / project root).

    Resolution order: ``CRAWLER_GITHUB_TOKEN_POOL`` > ``CRAWLER_GITHUB_TOKEN``
    > ``GITHUB_TOKEN`` (deprecated). See ``token_env`` for details.
    """
    tokens = resolve_github_tokens()
    if not tokens:
        # Look for .env file in current directory and project root
        env_file = Path('.env')
        if not env_file.exists():
            project_root = Path(__file__).parent.parent.parent
            env_file = project_root / '.env'

        if env_file.exists():
            load_dotenv(env_file)
            tokens = resolve_github_tokens()
            if tokens:
                console.print(f"[green]✓[/green] Loaded GitHub token(s) from {env_file}")

    if not tokens:
        console.print(f"[red]Error: {tokens_not_set_message()}[/red]")
        console.print(f"Set a single token:  export {TOKEN_ENV}='your_token_here'")
        console.print(f"Or a rotation pool:  export {POOL_ENV}='token1,token2,token3'")
        console.print(f"Or create a .env file in your project root with: {TOKEN_ENV}=your_token_here")
        sys.exit(1)

    return tokens


@app.command()
def crawl(
    seeds: Optional[List[str]] = typer.Argument(
        None,
        help="Initial seed nodes (users, orgs, or repos). Can be usernames, org/repo, or full GitHub URLs."
    ),
    seed_file: Optional[Path] = typer.Option(
        None,
        "--seed-file", "-f",
        help="Path to file containing seed nodes (one per line)",
        exists=True
    ),
    rounds: int = typer.Option(
        3,
        "--rounds", "-r",
        help="Number of BFS rounds to perform",
        min=1
    ),
    output_dir: Path = typer.Option(
        Path("output"),
        "--output-dir", "-o",
        help="Directory for output files"
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir", "-c",
        help="Directory for caching API responses "
             "(default: $OPC_CACHE_DIR or data/open-pulse-crawler/cache)"
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Disable API response caching (overrides --cache-dir and $OPC_CACHE_DIR)"
    ),
    state_file: Optional[Path] = typer.Option(
        None,
        "--state-file", "-s",
        help="File to save/load crawler state for resuming"
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume from saved state file"
    ),
    no_json: bool = typer.Option(
        False,
        "--no-json",
        help="Skip JSON output"
    ),
    no_csv: bool = typer.Option(
        False,
        "--no-csv",
        help="Skip CSV output"
    ),
    visualize: bool = typer.Option(
        False,
        "--visualize", "-v",
        help="Generate graph visualization (PNG)"
    ),
    visualize_clusters: bool = typer.Option(
        False,
        "--visualize-clusters",
        help="Generate separate visualizations for each disconnected cluster"
    ),
    exclude_bots: bool = typer.Option(
        False,
        "--exclude-bots",
        help="Exclude bots from visualization"
    ),
    show_unexplored: bool = typer.Option(
        False,
        "--show-unexplored",
        help="Include unexplored (discovered but not yet visited) nodes in visualizations"
    ),
    incremental_export: bool = typer.Option(
        False,
        "--incremental-export",
        help="Export graph data after each round (in addition to final export)"
    ),
    crawl_dependencies: bool = typer.Option(
        False,
        "--crawl-dependencies",
        help="Crawl repository dependencies (downstream) via SBOM"
    ),
    crawl_dependents: bool = typer.Option(
        False,
        "--crawl-dependents",
        help="Crawl repository dependents (upstream) via GitHub 'Used by' graph"
    ),
    min_stars: int = typer.Option(
        0,
        "--min-stars",
        help="Minimum stars for filtering dependents/dependencies"
    ),
    max_dependents: Optional[int] = typer.Option(
        None,
        "--max-dependents",
        help="Maximum number of dependents to fetch (default: None = all)"
    ),
    crawl_issues: bool = typer.Option(
        False,
        "--crawl-issues",
        help="Fetch issue authors and conversation commenters per repo (opt-in, can be expensive on busy repos)"
    ),
    crawl_prs: bool = typer.Option(
        False,
        "--crawl-prs",
        help="Fetch PR authors, conversation commenters, and reviewers per repo (opt-in, can be expensive on busy repos)"
    ),
    issue_max: int = typer.Option(
        100,
        "--issue-max",
        help="Maximum number of issues to scan per repo when --crawl-issues is enabled (most recent first)",
        min=1
    ),
    pr_max: int = typer.Option(
        100,
        "--pr-max",
        help="Maximum number of PRs to scan per repo when --crawl-prs is enabled (most recent first)",
        min=1
    ),
    max_contributors: Optional[int] = typer.Option(
        None,
        "--max-contributors",
        help=(
            "Optional per-repo contributor limit: take up to N contributors "
            "per repo (a repo with more is truncated to the top N, never "
            "skipped). Default: no cap — every contributor is taken."
        ),
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable verbose logging"
    ),
    request_delay: float = typer.Option(
        0.0,
        "--request-delay",
        help="Minimum delay (in seconds) between API requests (default: 0.0)",
        min=0.0
    ),
    max_concurrent: int = typer.Option(
        5,
        "--max-concurrent",
        help="Maximum number of concurrent API requests (default: 5)",
        min=1,
        max=20
    ),
    rate_limit_buffer: int = typer.Option(
        50,
        "--rate-limit-buffer",
        help="Number of requests to keep as buffer before waiting for rate limit (default: 50)",
        min=10,
        max=1000
    ),
    batch_size: Optional[int] = typer.Option(
        None,
        "--batch-size", "-b",
        help="Number of nodes to process concurrently (default: matches --max-concurrent)",
        min=1,
        max=50
    ),
    # ── Optional gimie hybrid repo discovery ──────────────────────────────
    gimie_repos: bool = typer.Option(
        False,
        "--gimie-repos",
        help="Populate repositories from gimie JSON-LD (keep user/org from GitHub API).",
    ),
    gimie_api_base: str = typer.Option(
        "http://host.docker.internal:1234",
        "--gimie-api-base",
        help="Base URL for the gimie JSON-LD API.",
    ),
    gimie_store_jsonld: bool = typer.Option(
        False,
        "--gimie-store-jsonld",
        help="Store raw gimie JSON-LD payloads under output-dir/jsonld/ during crawl.",
    ),
    gimie_skip_existing_jsonld: bool = typer.Option(
        False,
        "--gimie-skip-existing-jsonld",
        help="Skip HTTP when a payload already exists under output-dir/jsonld/ (crawler output only).",
    ),
):
    """
    Crawl GitHub to discover users, organizations, and repositories.
    
    Example usage:
    
        # Crawl from specific seeds
        open-pulse-crawler crawl caviri sdsc-ordes/gimie --rounds 2
        
        # Crawl from seed file
        open-pulse-crawler crawl --seed-file seeds.txt --rounds 3
        
        # Resume from saved state
        open-pulse-crawler crawl --resume --state-file state.json
    """
    setup_logging(verbose)
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Collect seeds
    all_seeds = list(seeds) if seeds else []
    if seed_file:
        all_seeds.extend(parse_seed_file(seed_file))
    
    if not all_seeds and not resume:
        console.print("[red]Error: No seed nodes provided[/red]")
        console.print("Provide seeds as arguments or use --seed-file option")
        raise typer.Exit(1)
    
    # Get GitHub tokens
    tokens = get_github_tokens()
    console.print(f"[green]✓[/green] Loaded {len(tokens)} GitHub token(s)")
    
    # Resolve cache directory: --cache-dir wins, else $OPC_CACHE_DIR / default.
    # --no-cache disables caching outright.
    if no_cache:
        cache_dir = None
        console.print("[yellow]●[/yellow] API response caching disabled (--no-cache)")
    else:
        cache_dir = resolve_cache_dir(cache_dir)
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            console.print(f"[green]✓[/green] Cache directory: {cache_dir}")
    
    # ── gimie hybrid repo option wiring ─────────────────────────────────────
    jsonld_dir: Optional[Path] = None
    if gimie_repos:
        if gimie_store_jsonld:
            jsonld_dir = output_dir / "jsonld"

    # ── gimie hybrid repo option wiring ─────────────────────────────────────
    jsonld_dir: Optional[Path] = None
    if gimie_repos:
        if gimie_store_jsonld:
            jsonld_dir = output_dir / "jsonld"

    # Initialize client and crawler
    client = GitHubClient(
        tokens, 
        cache_dir=cache_dir,
        request_delay=request_delay,
        max_concurrent_requests=max_concurrent,
        rate_limit_buffer=rate_limit_buffer
    )
    crawler = GitHubCrawler(
        client,
        max_rounds=rounds,
        state_file=state_file,
        batch_size=batch_size,
        crawl_dependencies=crawl_dependencies,
        crawl_dependents=crawl_dependents,
        crawl_issues=crawl_issues,
        crawl_prs=crawl_prs,
        issue_max=issue_max,
        pr_max=pr_max,
        min_stars=min_stars,
        max_dependents=max_dependents,
        max_contributors=max_contributors,
        gimie_repos=gimie_repos,
        gimie_api_base=gimie_api_base,
        gimie_store_jsonld_dir=jsonld_dir,
        gimie_skip_existing_jsonld=gimie_skip_existing_jsonld,
    )
    
    # Setup incremental export callback if requested
    if incremental_export:
        def export_callback(round_num):
            """Export data after each round."""
            try:
                round_dir = crawler.export_round(
                    output_dir,
                    round_num,
                    visualize=visualize,
                    visualize_clusters=visualize_clusters,
                    show_unexplored=show_unexplored,
                    no_json=no_json,
                    no_csv=no_csv
                )
                console.print(f"[green]✓[/green] Round {round_num} exported to {round_dir.name}/")
            except Exception as e:
                console.print(f"[red]✗[/red] Round {round_num} export failed: {e}")
                logger.error(f"Incremental export failed for round {round_num}: {e}")
        
        crawler.incremental_export_callback = export_callback
        console.print(f"[green]✓[/green] Incremental export enabled")
    
    # Resume or start fresh
    if resume and state_file and state_file.exists():
        console.print(f"[yellow]Resuming from state file: {state_file}[/yellow]")
        if crawler.load_state():
            console.print(f"[green]✓[/green] State loaded successfully")
        else:
            console.print("[red]Failed to load state, starting fresh[/red]")
            crawler.add_seeds(all_seeds)
    else:
        console.print(f"[green]✓[/green] Added {len(all_seeds)} seed nodes")
        crawler.add_seeds(all_seeds)
    
    # Start crawling
    console.print(f"\n[bold blue]Starting BFS crawl for {rounds} rounds...[/bold blue]\n")
    
    start_time = datetime.now()
    
    try:
        crawler.crawl()
    except KeyboardInterrupt:
        console.print("\n[yellow]Crawl interrupted by user[/yellow]")
        if state_file:
            crawler.save_state()
            console.print(f"[green]✓[/green] State saved to {state_file}")
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"\n[red]Error during crawl: {e}[/red]")
        if state_file:
            crawler.save_state()
            console.print(f"[green]✓[/green] State saved to {state_file}")
        raise typer.Exit(1)
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    # Display statistics
    stats = crawler.get_statistics()
    
    console.print(f"\n[bold green]Crawl completed in {duration:.1f} seconds[/bold green]\n")
    
    table = Table(title="Crawl Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="magenta")
    
    table.add_row("Rounds Completed", str(stats['rounds_completed']))
    table.add_row("Total Nodes Visited", str(stats['total_nodes']))
    
    # Show visited entities
    table.add_row("[bold]Visited Entities[/bold]", "")
    table.add_row("  Users Visited", str(stats['users']))
    table.add_row("  Organizations Visited", str(stats['organizations']))
    table.add_row("  Repositories Visited", str(stats['repositories']))
    
    # Show queued entities (discovered but not yet visited)
    if stats['round_stats']:
        last_round = stats['round_stats'][-1]
        if 'queued_users' in last_round:
            table.add_row("[bold]Queued for Next Round[/bold]", "")
            table.add_row("  Users Queued", str(last_round.get('queued_users', 0)))
            table.add_row("  Organizations Queued", str(last_round.get('queued_orgs', 0)))
            table.add_row("  Repositories Queued", str(last_round.get('queued_repos', 0)))
    
    if stats.get('api_stats') is not None:
        api_stats = stats['api_stats']
        table.add_row("[bold]API Statistics[/bold]", "")
        table.add_row("  API Calls Made", str(api_stats['api_calls']))
        table.add_row("  Cache Hits", str(api_stats['cache_hits']))
        table.add_row("  Rate Limit Waits", str(api_stats['rate_limit_waits']))
        table.add_row("  Token Switches", str(api_stats['token_switches']))
        table.add_row("  Throttle Waits", str(api_stats.get('throttle_waits', 0)))

        # Show efficiency metrics
        if 'efficiency' in api_stats:
            eff = api_stats['efficiency']
            table.add_row("Cache Hit Rate", f"{eff['cache_hit_rate']:.1f}%")
            table.add_row("Requests per Wait", f"{eff['requests_per_wait']:.1f}")
    
    console.print(table)
    
    # Round statistics
    if stats['round_stats']:
        console.print("\n[bold]Per-Round Statistics:[/bold]\n")
        console.print("[dim]Processed = entities fully explored this round | Queued = entities discovered but not yet explored[/dim]\n")
        
        round_table = Table()
        round_table.add_column("Round", style="cyan")
        round_table.add_column("Processed", style="magenta")
        round_table.add_column("Users", style="blue")
        round_table.add_column("Orgs", style="red")
        round_table.add_column("Repos", style="green")
        round_table.add_column("Queued", style="white")
        round_table.add_column("Time (s)", style="yellow")
        
        for rs in stats['round_stats']:
            # Format queue info if available
            queue_info = str(rs.get('queue_size', 0))
            if 'queued_users' in rs:
                queue_details = f"{rs['queued_users']}u/{rs['queued_orgs']}o/{rs['queued_repos']}r"
                queue_info = f"{rs['queue_size']}\n({queue_details})"
            
            round_table.add_row(
                str(rs['round']),
                str(rs['nodes_processed']),
                str(rs['users_found']),
                str(rs['orgs_found']),
                str(rs['repos_found']),
                queue_info,
                f"{rs['time_seconds']:.1f}"
            )
        
        console.print(round_table)
    
    # Export results
    console.print("\n[bold blue]Exporting results...[/bold blue]\n")
    
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    
    # JSON export
    if not no_json:
        json_path = output_dir / f"{timestamp}.graph.json"
        export_to_json(crawler.graph, json_path)
        console.print(f"[green]✓[/green] JSON: {json_path}")
    
    # CSV export
    if not no_csv:
        edges_csv_path = output_dir / f"{timestamp}.edges.csv"
        export_to_csv(crawler.graph, edges_csv_path, crawler.seed_nodes)
        console.print(f"[green]✓[/green] CSV (edges): {edges_csv_path}")
        
        nodes_csv_path = output_dir / f"{timestamp}.nodes.csv"
        export_nodes_csv(
            crawler.graph,
            nodes_csv_path,
            crawler.seed_nodes,
            discovered_nodes=crawler.discovered_nodes,
        )
        console.print(f"[green]✓[/green] CSV (nodes): {nodes_csv_path}")
    
    # Visualization
    if visualize or visualize_clusters:
        if not VISUALIZATION_AVAILABLE:
            console.print("[yellow]⚠[/yellow] Visualization skipped: networkx/matplotlib not installed")
            console.print("Install with: pip install networkx matplotlib")
        else:
            if visualize:
                viz_path = output_dir / f"{timestamp}.graph.png"
                console.print(f"[blue]Generating main visualization...[/blue]")
                try:
                    discovered = crawler.discovered_nodes if show_unexplored else None
                    visualize_graph(crawler.graph, viz_path, crawler.seed_nodes, crawler.visited, discovered, exclude_bots=exclude_bots)
                    console.print(f"[green]✓[/green] Visualization: {viz_path}")
                except Exception as e:
                    console.print(f"[red]✗[/red] Visualization failed: {e}")
            
            if visualize_clusters:
                clusters_dir = output_dir / f"{timestamp}.clusters"
                console.print(f"[blue]Generating cluster visualizations...[/blue]")
                try:
                    discovered = crawler.discovered_nodes if show_unexplored else None
                    viz_clusters(crawler.graph, clusters_dir, crawler.seed_nodes, crawler.visited, discovered, exclude_bots=exclude_bots)
                    console.print(f"[green]✓[/green] Cluster visualizations: {clusters_dir}/")
                except Exception as e:
                    console.print(f"[red]✗[/red] Cluster visualization failed: {e}")
    
    console.print(f"\n[bold green]All done! 🎉[/bold green]")


@app.command()
def version():
    """Show version information."""
    from . import __version__
    console.print(f"Open Pulse Crawler version {__version__}")


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
