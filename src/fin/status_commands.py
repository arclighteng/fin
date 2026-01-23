# status_commands.py
"""
User-facing status and drill commands.

These are the primary interface for users to understand their financial situation.
"""
import calendar
from datetime import date

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import db as dbmod
from .classify import detect_alerts, summarize_month, MonthSummary
from .config import load_config
from .log import setup_logging

console = Console()


def _fmt_money(cents: int, show_sign: bool = False) -> str:
    """Format cents as dollars."""
    if show_sign and cents >= 0:
        return f"+${cents/100:,.2f}"
    elif cents < 0:
        return f"-${abs(cents)/100:,.2f}"
    else:
        return f"${cents/100:,.2f}"


def _fmt_month(year: int, month: int) -> str:
    """Format month name."""
    return f"{calendar.month_name[month].upper()} {year}"


def _verdict(summary: MonthSummary) -> tuple[str, str, str]:
    """
    Return (emoji, short_verdict, explanation) based on financial state.
    """
    baseline = summary.baseline_cents
    net = summary.net_cents
    
    if baseline < 0:
        # Structural deficit
        shortfall = abs(baseline)
        return (
            "[red]✗[/red]",
            "[red]Not sustainable[/red]",
            f"Your recurring expenses exceed your income by {_fmt_money(shortfall)}/month. "
            f"This requires changes to recurring expenses or income."
        )
    elif baseline >= 0 and net < 0:
        # Sustainable but overspent this month
        overspend = abs(net)
        return (
            "[yellow]![/yellow]",
            "[yellow]Dipped into savings[/yellow]",
            f"Your baseline is healthy with {_fmt_money(baseline)} buffer. "
            f"This month's one-off spending exceeded that by {_fmt_money(overspend)}."
        )
    elif baseline > 0 and net >= 0:
        # All good
        return (
            "[green]✓[/green]",
            "[green]On track[/green]",
            f"You saved {_fmt_money(net)} this month. "
            f"Your lifestyle costs less than you earn."
        )
    else:
        # Break-even
        return (
            "[yellow]~[/yellow]",
            "[yellow]Break-even[/yellow]",
            "You're covering expenses but not building savings."
        )


def _severity_style(severity: str) -> str:
    """Return rich style for alert severity."""
    return {"high": "red", "medium": "yellow", "low": "dim"}.get(severity, "")


def status_command(
    month: str = typer.Option(None, help="Month to analyze (YYYY-MM). Defaults to current month."),
):
    """
    Your financial status at a glance.
    
    Shows whether you can sustain your lifestyle on your income,
    how much you're saving, and what needs attention.
    """
    cfg = load_config()
    setup_logging(cfg)
    
    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)
    
    try:
        # Parse month
        if month:
            try:
                year, mon = map(int, month.split("-"))
            except ValueError:
                console.print("[red]Invalid month format. Use YYYY-MM.[/red]")
                raise typer.Exit(1)
        else:
            today = date.today()
            year, mon = today.year, today.month
        
        # Check for data
        count = conn.execute("SELECT COUNT(*) as c FROM transactions").fetchone()["c"]
        if count == 0:
            console.print("[yellow]No transaction data yet. Run 'fin sync' first.[/yellow]")
            raise typer.Exit(1)
        
        # Get summary and alerts
        summary = summarize_month(conn, year, mon)
        alerts = detect_alerts(conn, year, mon)
        
        # Build display
        emoji, verdict_short, verdict_long = _verdict(summary)
        
        console.print()
        console.print(Panel(
            f"[bold]{_fmt_month(year, mon)}[/bold]",
            style="blue",
            expand=False,
        ))
        console.print()
        
        # Main numbers
        console.print("[bold]MONTHLY BASELINE[/bold]")
        console.print(f"  Income                         {_fmt_money(summary.income_cents):>12}")
        console.print(f"  Recurring expenses             {_fmt_money(-summary.recurring_cents):>12}")
        console.print(f"                                 {'─' * 12}")
        baseline_style = "green" if summary.baseline_cents >= 0 else "red"
        console.print(f"  [bold]Baseline[/bold]                       [{baseline_style}]{_fmt_money(summary.baseline_cents):>12}[/{baseline_style}]")
        console.print()
        
        console.print("[bold]THIS MONTH[/bold]")
        console.print(f"  Baseline                       {_fmt_money(summary.baseline_cents):>12}")
        console.print(f"  One-off spending               {_fmt_money(-summary.one_off_cents):>12}")
        console.print(f"                                 {'─' * 12}")
        net_style = "green" if summary.net_cents >= 0 else "red"
        console.print(f"  [bold]Net[/bold]                            [{net_style}]{_fmt_money(summary.net_cents):>12}[/{net_style}]")
        console.print()
        
        # Verdict
        console.print(f"  {emoji} {verdict_short}")
        console.print()
        console.print(Panel(verdict_long, style="dim", expand=False))
        console.print()
        
        # Note about transfers if any
        if summary.transfer_cents > 0:
            console.print(f"[dim]  Note: {_fmt_money(summary.transfer_cents)} in transfers (credit card payments, etc.) excluded from expenses.[/dim]")
            console.print()
        
        # Alerts
        if alerts:
            console.print("[bold]ALERTS[/bold]")
            for alert in alerts[:5]:
                style = _severity_style(alert.severity)
                icon = {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(alert.severity, "•")
                console.print(f"  {icon} [{style}]{alert.title}[/{style}]")
                console.print(f"      [dim]{alert.detail}[/dim]")
            if len(alerts) > 5:
                console.print(f"      [dim]...and {len(alerts) - 5} more. Run 'fin drill alerts' for all.[/dim]")
            console.print()
        
        # Quick breakdown
        console.print("[bold]TOP SPENDING[/bold]")
        
        # Two columns: recurring and one-off
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Recurring", style="dim")
        table.add_column("Amount", justify="right")
        table.add_column("One-off", style="dim")
        table.add_column("Amount", justify="right")
        
        recurring_top = summary.recurring_expenses[:5]
        one_off_top = summary.one_off_expenses[:5]
        max_rows = max(len(recurring_top), len(one_off_top))
        
        for i in range(max_rows):
            rec_name = rec_amt = one_name = one_amt = ""
            
            if i < len(recurring_top):
                name, cents, cadence = recurring_top[i]
                rec_name = name[:25] + "..." if len(name) > 28 else name
                rec_amt = _fmt_money(cents)
            
            if i < len(one_off_top):
                name, cents, count = one_off_top[i]
                suffix = f" ({count}x)" if count > 1 else ""
                display = name[:22] + "..." if len(name) > 25 else name
                one_name = display + suffix
                one_amt = _fmt_money(cents)
            
            table.add_row(rec_name, rec_amt, one_name, one_amt)
        
        console.print(table)
        console.print()
        
        # Footer
        console.print("[dim]For details: fin drill recurring | fin drill one-offs | fin drill alerts[/dim]")
        console.print()
        
    finally:
        conn.close()


def drill_command(
    area: str = typer.Argument(..., help="Area to drill into: recurring, one-offs, alerts, income, transfers"),
    month: str = typer.Option(None, help="Month to analyze (YYYY-MM). Defaults to current month."),
    limit: int = typer.Option(25, help="Number of items to show."),
):
    """
    Detailed breakdown of a specific area.
    
    Areas:
      recurring  - All recurring expenses with cadence patterns
      one-offs   - Discretionary spending this month
      alerts     - All alerts with details
      income     - Income sources
      transfers  - Credit card payments, internal transfers (excluded from analysis)
    """
    cfg = load_config()
    setup_logging(cfg)
    
    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)
    
    try:
        # Parse month
        if month:
            try:
                year, mon = map(int, month.split("-"))
            except ValueError:
                console.print("[red]Invalid month format. Use YYYY-MM.[/red]")
                raise typer.Exit(1)
        else:
            today = date.today()
            year, mon = today.year, today.month
        
        summary = summarize_month(conn, year, mon)
        
        console.print()
        console.print(f"[bold]{_fmt_month(year, mon)} - {area.upper()}[/bold]")
        console.print()
        
        if area == "recurring":
            if not summary.recurring_expenses:
                console.print("[dim]No recurring expenses detected.[/dim]")
                return
            
            table = Table(show_header=True, header_style="bold")
            table.add_column("Merchant", style="dim")
            table.add_column("Amount", justify="right")
            table.add_column("Cadence")
            
            total = 0
            for name, cents, cadence in summary.recurring_expenses[:limit]:
                table.add_row(name, _fmt_money(cents), cadence)
                total += cents
            
            console.print(table)
            console.print()
            console.print(f"[bold]Total recurring:[/bold] {_fmt_money(total)}")
            
            if len(summary.recurring_expenses) > limit:
                console.print(f"[dim]Showing {limit} of {len(summary.recurring_expenses)}. Use --limit to see more.[/dim]")
        
        elif area == "one-offs":
            if not summary.one_off_expenses:
                console.print("[dim]No one-off expenses this month.[/dim]")
                return
            
            table = Table(show_header=True, header_style="bold")
            table.add_column("Merchant", style="dim")
            table.add_column("Amount", justify="right")
            table.add_column("Count", justify="right")
            
            total = 0
            for name, cents, count in summary.one_off_expenses[:limit]:
                table.add_row(name, _fmt_money(cents), str(count))
                total += cents
            
            console.print(table)
            console.print()
            console.print(f"[bold]Total one-offs:[/bold] {_fmt_money(total)}")
            
            if len(summary.one_off_expenses) > limit:
                console.print(f"[dim]Showing {limit} of {len(summary.one_off_expenses)}. Use --limit to see more.[/dim]")
        
        elif area == "alerts":
            alerts = detect_alerts(conn, year, mon)
            
            if not alerts:
                console.print("[green]No alerts. Looking good![/green]")
                return
            
            for alert in alerts[:limit]:
                style = _severity_style(alert.severity)
                icon = {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(alert.severity, "•")
                console.print(f"{icon} [{style}][bold]{alert.title}[/bold][/{style}]")
                console.print(f"   {alert.detail}")
                if alert.amount_cents:
                    console.print(f"   [dim]Impact: {_fmt_money(alert.amount_cents)}[/dim]")
                console.print()
            
            if len(alerts) > limit:
                console.print(f"[dim]Showing {limit} of {len(alerts)}. Use --limit to see more.[/dim]")
        
        elif area == "income":
            if not summary.income_sources:
                console.print("[dim]No income recorded this month.[/dim]")
                return
            
            table = Table(show_header=True, header_style="bold")
            table.add_column("Source", style="dim")
            table.add_column("Amount", justify="right")
            
            total = 0
            for name, cents in summary.income_sources[:limit]:
                table.add_row(name, _fmt_money(cents))
                total += cents
            
            console.print(table)
            console.print()
            console.print(f"[bold]Total income:[/bold] {_fmt_money(total)}")
        
        elif area == "transfers":
            if not summary.transfers:
                console.print("[dim]No transfers this month.[/dim]")
                return
            
            console.print("[dim]Transfers are excluded from expense analysis (credit card payments, etc.)[/dim]")
            console.print()
            
            table = Table(show_header=True, header_style="bold")
            table.add_column("Description", style="dim")
            table.add_column("Amount", justify="right")
            
            total = 0
            for name, cents in summary.transfers[:limit]:
                table.add_row(name, _fmt_money(cents))
                total += cents
            
            console.print(table)
            console.print()
            console.print(f"[bold]Total transfers:[/bold] {_fmt_money(total)}")
        
        else:
            console.print(f"[red]Unknown area: {area}[/red]")
            console.print("Valid areas: recurring, one-offs, alerts, income, transfers")
            raise typer.Exit(1)
        
        console.print()
        
    finally:
        conn.close()


def trend_command(
    months: int = typer.Option(3, help="Number of months to compare."),
):
    """
    Show trends over recent months.
    
    Helps answer: Am I getting better or worse?
    """
    cfg = load_config()
    setup_logging(cfg)
    
    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)
    
    try:
        today = date.today()
        summaries: list[MonthSummary] = []
        
        for i in range(months):
            # Go back i months
            year = today.year
            month = today.month - i
            while month <= 0:
                month += 12
                year -= 1
            
            try:
                s = summarize_month(conn, year, month)
                if s.income_cents > 0 or s.recurring_cents > 0 or s.one_off_cents > 0:
                    summaries.append(s)
            except Exception:
                pass
        
        if not summaries:
            console.print("[yellow]Not enough data for trends. Run 'fin sync' with more history.[/yellow]")
            raise typer.Exit(1)
        
        summaries.reverse()  # Oldest first
        
        console.print()
        console.print("[bold]MONTHLY TRENDS[/bold]")
        console.print()
        
        table = Table(show_header=True, header_style="bold")
        table.add_column("Month")
        table.add_column("Income", justify="right")
        table.add_column("Recurring", justify="right")
        table.add_column("One-offs", justify="right")
        table.add_column("Net", justify="right")
        table.add_column("Status")
        
        for s in summaries:
            net_style = "green" if s.net_cents >= 0 else "red"
            status = "[green]✓[/green]" if s.is_sustainable else "[red]✗[/red]"
            
            table.add_row(
                f"{calendar.month_abbr[s.month]} {s.year}",
                _fmt_money(s.income_cents),
                _fmt_money(s.recurring_cents),
                _fmt_money(s.one_off_cents),
                f"[{net_style}]{_fmt_money(s.net_cents)}[/{net_style}]",
                status,
            )
        
        console.print(table)
        console.print()
        
        # Summary insights
        if len(summaries) >= 2:
            latest = summaries[-1]
            prev = summaries[-2]
            
            income_diff = latest.income_cents - prev.income_cents
            recurring_diff = latest.recurring_cents - prev.recurring_cents
            
            console.print("[bold]Month-over-month:[/bold]")
            if abs(income_diff) > 1000:  # > $10 change
                direction = "up" if income_diff > 0 else "down"
                style = "green" if income_diff > 0 else "red"
                console.print(f"  Income [{style}]{direction} {_fmt_money(abs(income_diff))}[/{style}]")
            
            if abs(recurring_diff) > 1000:
                direction = "up" if recurring_diff > 0 else "down"
                style = "red" if recurring_diff > 0 else "green"  # Inverse: more spending is bad
                console.print(f"  Recurring [{style}]{direction} {_fmt_money(abs(recurring_diff))}[/{style}]")
        
        console.print()
        
    finally:
        conn.close()
