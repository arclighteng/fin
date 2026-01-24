# classify.py
"""
Transaction classification engine.

Classifies transactions into:
- income: positive amounts
- recurring: merchants with regular patterns OR high-frequency habitual spending
- transfer: credit card payments, internal transfers (excluded from expense analysis)
- one-off: everything else
"""
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta


# ---------------------------------------------------------------------------
# Known Subscription Services Registry
# ---------------------------------------------------------------------------
# These are well-known subscription services that should be flagged as
# subscriptions even with just 1-2 charges (no need to wait for 3+ occurrences).
#
# IMPORTANT: Patterns are matched longest-first to ensure specific services
# like "youtube tv" match before generic "youtube". Each pattern should be
# as specific as needed to avoid false matches.
#
# Format: pattern -> (display_name, typical_cadence, is_distinct)
# is_distinct=True means this is a separate service even if another pattern
# might also match (e.g., "youtube tv" is distinct from "youtube premium")

KNOWN_SUBSCRIPTIONS: dict[str, tuple[str, str]] = {
    # === Google/YouTube Services (MUST BE DISTINCT) ===
    # These are separate products and should NOT be flagged as duplicates
    "youtube tv": ("YouTube TV", "monthly"),           # Live TV streaming ~$73/mo
    "youtubetv": ("YouTube TV", "monthly"),
    "youtube premium": ("YouTube Premium", "monthly"), # Ad-free + Music ~$14/mo
    "youtube music": ("YouTube Music", "monthly"),     # Music only ~$11/mo
    "yt premium": ("YouTube Premium", "monthly"),
    "yt music": ("YouTube Music", "monthly"),
    "youtube membership": ("YouTube Membership", "monthly"),  # Channel memberships
    "google fiber": ("Google Fiber", "monthly"),       # Internet service
    "google fi": ("Google Fi", "monthly"),             # Phone service
    "google one": ("Google One", "monthly"),           # Cloud storage
    "google workspace": ("Google Workspace", "monthly"),
    "google play": ("Google Play", "monthly"),         # App/media purchases
    "google storage": ("Google One", "monthly"),

    # === Streaming Video ===
    "netflix": ("Netflix", "monthly"),
    "hulu": ("Hulu", "monthly"),
    "disney plus": ("Disney+", "monthly"),
    "disney+": ("Disney+", "monthly"),
    "disneyplus": ("Disney+", "monthly"),
    "hbo max": ("Max", "monthly"),
    "hbomax": ("Max", "monthly"),
    "max.com": ("Max", "monthly"),
    "paramount plus": ("Paramount+", "monthly"),
    "paramount+": ("Paramount+", "monthly"),
    "paramountplus": ("Paramount+", "monthly"),
    "peacock": ("Peacock", "monthly"),
    "apple tv": ("Apple TV+", "monthly"),
    "appletv": ("Apple TV+", "monthly"),
    "prime video": ("Prime Video", "monthly"),
    "primevideo": ("Prime Video", "monthly"),
    "amazon prime": ("Amazon Prime", "monthly"),
    "crunchyroll": ("Crunchyroll", "monthly"),
    "funimation": ("Funimation", "monthly"),
    "discovery+": ("Discovery+", "monthly"),
    "discoveryplus": ("Discovery+", "monthly"),
    "espn+": ("ESPN+", "monthly"),
    "espnplus": ("ESPN+", "monthly"),
    "showtime": ("Showtime", "monthly"),
    "starz": ("Starz", "monthly"),
    "mgm+": ("MGM+", "monthly"),
    "britbox": ("BritBox", "monthly"),
    "acorn tv": ("Acorn TV", "monthly"),
    "shudder": ("Shudder", "monthly"),
    "curiosity stream": ("CuriosityStream", "monthly"),
    "mubi": ("Mubi", "monthly"),
    "criterion": ("Criterion Channel", "monthly"),

    # === Streaming Audio ===
    "spotify": ("Spotify", "monthly"),
    "apple music": ("Apple Music", "monthly"),
    "amazon music": ("Amazon Music", "monthly"),
    "pandora": ("Pandora", "monthly"),
    "tidal": ("Tidal", "monthly"),
    "deezer": ("Deezer", "monthly"),
    "audible": ("Audible", "monthly"),
    "sirius": ("SiriusXM", "monthly"),
    "siriusxm": ("SiriusXM", "monthly"),

    # === Cloud Storage ===
    "icloud": ("iCloud", "monthly"),
    "dropbox": ("Dropbox", "monthly"),
    "onedrive": ("OneDrive", "monthly"),
    "box.com": ("Box", "monthly"),
    "backblaze": ("Backblaze", "monthly"),
    "carbonite": ("Carbonite", "monthly"),
    "idrive": ("iDrive", "monthly"),

    # === Software/Productivity ===
    "adobe": ("Adobe Creative Cloud", "monthly"),
    "creative cloud": ("Adobe Creative Cloud", "monthly"),
    "microsoft 365": ("Microsoft 365", "monthly"),
    "office 365": ("Microsoft 365", "monthly"),
    "m365": ("Microsoft 365", "monthly"),
    "github": ("GitHub", "monthly"),
    "gitlab": ("GitLab", "monthly"),
    "chatgpt": ("ChatGPT Plus", "monthly"),
    "openai": ("OpenAI", "monthly"),
    "claude": ("Claude Pro", "monthly"),
    "anthropic": ("Claude Pro", "monthly"),
    "notion": ("Notion", "monthly"),
    "evernote": ("Evernote", "monthly"),
    "todoist": ("Todoist", "monthly"),
    "1password": ("1Password", "monthly"),
    "lastpass": ("LastPass", "monthly"),
    "dashlane": ("Dashlane", "monthly"),
    "bitwarden": ("Bitwarden", "monthly"),
    "keeper": ("Keeper", "monthly"),
    "zoom": ("Zoom", "monthly"),
    "slack": ("Slack", "monthly"),
    "figma": ("Figma", "monthly"),
    "sketch": ("Sketch", "monthly"),
    "invision": ("InVision", "monthly"),
    "jetbrains": ("JetBrains", "monthly"),
    "intellij": ("JetBrains", "monthly"),

    # === Professional/Learning ===
    "linkedin premium": ("LinkedIn Premium", "monthly"),
    "linkedin learning": ("LinkedIn Learning", "monthly"),
    "coursera": ("Coursera", "monthly"),
    "udemy": ("Udemy", "monthly"),
    "skillshare": ("Skillshare", "monthly"),
    "masterclass": ("MasterClass", "annual"),
    "duolingo": ("Duolingo", "monthly"),
    "babbel": ("Babbel", "monthly"),
    "rosetta stone": ("Rosetta Stone", "monthly"),
    "brilliant": ("Brilliant", "monthly"),
    "codecademy": ("Codecademy", "monthly"),
    "pluralsight": ("Pluralsight", "monthly"),
    "o'reilly": ("O'Reilly", "monthly"),
    "safari books": ("O'Reilly", "monthly"),
    "blinkist": ("Blinkist", "monthly"),
    "headspace": ("Headspace", "monthly"),
    "calm.com": ("Calm", "monthly"),
    "calm app": ("Calm", "monthly"),

    # === Gaming ===
    "xbox game pass": ("Xbox Game Pass", "monthly"),
    "xbox live": ("Xbox Live", "monthly"),
    "xbox": ("Xbox", "monthly"),
    "playstation plus": ("PlayStation Plus", "monthly"),
    "playstation now": ("PlayStation Now", "monthly"),
    "ps plus": ("PlayStation Plus", "monthly"),
    "psn": ("PlayStation", "monthly"),
    "nintendo online": ("Nintendo Online", "monthly"),
    "nintendo switch online": ("Nintendo Online", "monthly"),
    "steampowered": ("Steam", "monthly"),
    "steam games": ("Steam", "monthly"),
    "steam purchase": ("Steam", "monthly"),
    "ea play": ("EA Play", "monthly"),
    "ea sports": ("EA Play", "monthly"),
    "ubisoft": ("Ubisoft+", "monthly"),
    "humble bundle": ("Humble Bundle", "monthly"),
    "geforce now": ("GeForce Now", "monthly"),
    "nvidia": ("GeForce Now", "monthly"),

    # === News/Reading ===
    "nytimes": ("NY Times", "monthly"),
    "new york times": ("NY Times", "monthly"),
    "nyt ": ("NY Times", "monthly"),
    "washington post": ("Washington Post", "monthly"),
    "wapo": ("Washington Post", "monthly"),
    "wall street journal": ("Wall Street Journal", "monthly"),
    "wsj": ("Wall Street Journal", "monthly"),
    "economist": ("The Economist", "monthly"),
    "atlantic": ("The Atlantic", "monthly"),
    "new yorker": ("The New Yorker", "monthly"),
    "wired": ("Wired", "monthly"),
    "medium": ("Medium", "monthly"),
    "substack": ("Substack", "monthly"),
    "kindle unlimited": ("Kindle Unlimited", "monthly"),
    "scribd": ("Scribd", "monthly"),
    "apple news": ("Apple News+", "monthly"),

    # === Fitness ===
    "peloton": ("Peloton", "monthly"),
    "planet fitness": ("Planet Fitness", "monthly"),
    "la fitness": ("LA Fitness", "monthly"),
    "24 hour fitness": ("24 Hour Fitness", "monthly"),
    "anytime fitness": ("Anytime Fitness", "monthly"),
    "orangetheory": ("Orangetheory", "monthly"),
    "equinox": ("Equinox", "monthly"),
    "gold's gym": ("Gold's Gym", "monthly"),
    "ymca": ("YMCA", "monthly"),
    "strava": ("Strava", "monthly"),
    "fitbit premium": ("Fitbit Premium", "monthly"),
    "apple fitness": ("Apple Fitness+", "monthly"),
    "beachbody": ("Beachbody", "monthly"),
    "myfitnesspal": ("MyFitnessPal", "monthly"),
    "noom": ("Noom", "monthly"),
    "whoop": ("Whoop", "monthly"),

    # === VPN/Security ===
    "nordvpn": ("NordVPN", "monthly"),
    "expressvpn": ("ExpressVPN", "monthly"),
    "surfshark": ("Surfshark", "monthly"),
    "protonvpn": ("ProtonVPN", "monthly"),
    "proton vpn": ("ProtonVPN", "monthly"),
    "private internet": ("Private Internet Access", "monthly"),
    "cyberghost": ("CyberGhost", "monthly"),
    "tunnelbear": ("TunnelBear", "monthly"),
    "mullvad": ("Mullvad", "monthly"),
    "norton": ("Norton", "monthly"),
    "mcafee": ("McAfee", "monthly"),
    "malwarebytes": ("Malwarebytes", "monthly"),
    "avast": ("Avast", "monthly"),
    "kaspersky": ("Kaspersky", "monthly"),
    "bitdefender": ("Bitdefender", "monthly"),
    "lifelock": ("LifeLock", "monthly"),
    "identity guard": ("Identity Guard", "monthly"),

    # === Creator/Social ===
    "patreon": ("Patreon", "monthly"),
    "twitch": ("Twitch", "monthly"),
    "discord nitro": ("Discord Nitro", "monthly"),
    "onlyfans": ("OnlyFans", "monthly"),
    "ko-fi": ("Ko-fi", "monthly"),
    "buy me a coffee": ("Buy Me a Coffee", "monthly"),
    "gumroad": ("Gumroad", "monthly"),

    # === Design/Creative Tools ===
    "grammarly": ("Grammarly", "monthly"),
    "canva": ("Canva", "monthly"),
    "shutterstock": ("Shutterstock", "monthly"),
    "getty images": ("Getty Images", "monthly"),
    "envato": ("Envato", "monthly"),
    "creative market": ("Creative Market", "monthly"),

    # === Home Security ===
    # Note: Use specific patterns to avoid false matches (e.g., "ring" in "Burger King")
    "ring protect": ("Ring Protect", "monthly"),
    "ring alarm": ("Ring Protect", "monthly"),
    "ring.com": ("Ring Protect", "monthly"),
    "nest aware": ("Nest Aware", "monthly"),
    "nest cam": ("Nest Aware", "monthly"),
    "google nest": ("Nest Aware", "monthly"),
    "arlo secure": ("Arlo Secure", "monthly"),
    "arlo smart": ("Arlo Secure", "monthly"),
    "arlo.com": ("Arlo Secure", "monthly"),
    "simplisafe": ("SimpliSafe", "monthly"),
    "adt security": ("ADT", "monthly"),
    "adt pulse": ("ADT", "monthly"),
    "vivint": ("Vivint", "monthly"),
}

# Pre-sort patterns by length (longest first) for matching
_SORTED_SUBSCRIPTION_PATTERNS: list[tuple[str, str, str]] = sorted(
    [(pattern, name, cadence) for pattern, (name, cadence) in KNOWN_SUBSCRIPTIONS.items()],
    key=lambda x: len(x[0]),
    reverse=True,
)


def _match_known_subscription(merchant_norm: str) -> tuple[str, str] | None:
    """
    Check if a merchant matches any known subscription service.
    Returns (display_name, cadence) if matched, None otherwise.

    Matches longest patterns first to ensure specific services like
    "youtube tv" match before generic patterns.
    """
    merchant_lower = merchant_norm.lower()
    for pattern, display_name, cadence in _SORTED_SUBSCRIPTION_PATTERNS:
        if pattern in merchant_lower:
            return (display_name, cadence)
    return None


@dataclass
class MerchantPattern:
    """Detected pattern for a merchant."""
    merchant_norm: str
    occurrence_count: int
    median_amount_cents: int
    median_interval_days: int | None
    is_recurring: bool
    is_habitual: bool  # High-frequency but irregular (groceries, Amazon)
    is_transfer: bool  # Credit card payment, internal transfer
    cadence_label: str  # "weekly", "monthly", "annual", "habitual", ""
    first_seen: date
    last_seen: date
    # Amount variance metrics for pattern-based classification
    amount_cv: float  # Coefficient of variation (std/mean) - 0.0 = fixed, >0.1 = variable
    amount_min_cents: int
    amount_max_cents: int


@dataclass
class ClassifiedTransaction:
    """A transaction with its classification."""
    posted_at: date
    amount_cents: int
    merchant_norm: str
    raw_description: str
    classification: str  # "income", "recurring", "transfer", "one-off"
    merchant_pattern: MerchantPattern | None


def _normalize_merchant(merchant: str | None, description: str | None) -> str:
    """Normalize merchant/description for grouping."""
    raw = merchant or description or ""
    return raw.strip().lower()


def _is_transfer(merchant_norm: str) -> bool:
    """
    Detect if a transaction is a transfer/payment rather than actual spending.
    
    These are excluded from expense analysis because:
    - Credit card payments: actual spending is on the card, not the payment
    - Internal transfers: moving money between accounts isn't spending
    """
    transfer_keywords = [
        "credit card",
        "card payment",
        "cc payment",
        "payment to chase",
        "payment to citi",
        "payment to amex",
        "payment to american express",
        "payment to discover",
        "payment to capital one",
        "payment to bank of america",
        "payment to wells fargo",
        "payment to usaa",
        "bill pay",
        "autopay",
        "transfer to",
        "transfer from",
        "ach transfer",
        "internal transfer",
        "zelle",  # Person-to-person, not a merchant expense
        "venmo",
        "cash app",
    ]
    return any(kw in merchant_norm for kw in transfer_keywords)


def _detect_patterns(
    conn: sqlite3.Connection,
    lookback_days: int = 400,
    account_filter: list[str] | None = None,
) -> dict[str, MerchantPattern]:
    """
    Analyze transaction history to detect recurring merchants.

    A merchant is "recurring" if either:
    1. Subscription-like: 3+ occurrences with predictable cadence (weekly/monthly/annual)
    2. Habitual: 6+ occurrences in the period (groceries, gas, Amazon - frequent but irregular)

    Transfers (credit card payments, etc.) are flagged separately.
    """
    since = (date.today() - timedelta(days=lookback_days)).isoformat()

    # Build query with optional account filter
    query = """
        SELECT
            posted_at,
            amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS merchant_norm
        FROM transactions
        WHERE posted_at >= ?
          AND amount_cents < 0
          AND merchant_norm <> ''
    """
    params: list = [since]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND account_id IN ({placeholders})"
        params.extend(account_filter)

    query += " ORDER BY merchant_norm, posted_at"
    rows = conn.execute(query, params).fetchall()
    
    # Group by merchant
    by_merchant: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for r in rows:
        d = datetime.fromisoformat(r["posted_at"]).date()
        by_merchant[r["merchant_norm"]].append((d, abs(r["amount_cents"])))
    
    patterns: dict[str, MerchantPattern] = {}
    
    for merchant_norm, items in by_merchant.items():
        items.sort(key=lambda x: x[0])
        dates = [d for d, _ in items]
        amounts = [a for _, a in items]
        
        # Check if this is a transfer
        is_transfer = _is_transfer(merchant_norm)
        
        # Calculate intervals
        intervals = []
        if len(dates) >= 2:
            intervals = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
        
        # Determine if recurring (subscription-like cadence)
        is_subscription_recurring = False
        cadence_label = ""
        median_interval = None
        
        if len(items) >= 3 and intervals:
            median_interval = int(statistics.median(intervals))
            
            # Check interval consistency for subscription detection
            mad = statistics.median([abs(i - median_interval) for i in intervals])
            tolerance = max(7, median_interval * 0.3)  # 30% or 7 days
            
            if mad <= tolerance and 5 <= median_interval <= 400:
                is_subscription_recurring = True
                
                # Label the cadence
                if 5 <= median_interval <= 10:
                    cadence_label = "weekly"
                elif 12 <= median_interval <= 18:
                    cadence_label = "biweekly"
                elif 25 <= median_interval <= 35:
                    cadence_label = "monthly"
                elif 55 <= median_interval <= 70:
                    cadence_label = "bimonthly"
                elif 85 <= median_interval <= 100:
                    cadence_label = "quarterly"
                elif 330 <= median_interval <= 400:
                    cadence_label = "annual"
                else:
                    cadence_label = "regular"
        
        # Determine if habitual (high-frequency, irregular - groceries, Amazon, gas)
        # 6+ times in the lookback period = habitual spending
        is_habitual = False
        if not is_subscription_recurring and len(items) >= 6:
            is_habitual = True
            cadence_label = "habitual"
        
        is_recurring = is_subscription_recurring or is_habitual
        
        # Calculate amount variance (coefficient of variation)
        amount_cv = 0.0
        if len(amounts) >= 2:
            mean_amt = statistics.mean(amounts)
            if mean_amt > 0:
                std_amt = statistics.stdev(amounts)
                amount_cv = std_amt / mean_amt

        patterns[merchant_norm] = MerchantPattern(
            merchant_norm=merchant_norm,
            occurrence_count=len(items),
            median_amount_cents=int(statistics.median(amounts)) if amounts else 0,
            median_interval_days=median_interval,
            is_recurring=is_recurring,
            is_habitual=is_habitual,
            is_transfer=is_transfer,
            cadence_label=cadence_label,
            first_seen=dates[0] if dates else date.today(),
            last_seen=dates[-1] if dates else date.today(),
            amount_cv=amount_cv,
            amount_min_cents=min(amounts) if amounts else 0,
            amount_max_cents=max(amounts) if amounts else 0,
        )
    
    return patterns


# ---------------------------------------------------------------------------
# Pattern-Based Classification
# ---------------------------------------------------------------------------
# This uses transaction patterns (cadence, amount variance, amount range) to
# classify merchants, rather than relying solely on keyword matching.

def classify_by_pattern(
    pattern: MerchantPattern,
    merchant_norm: str,
) -> tuple[str, float]:
    """
    Classify a merchant based on its transaction pattern characteristics.

    This is the PRIMARY classification method - keywords are secondary boosters.

    Returns: (transaction_type, confidence)
        - "subscription": Fixed-amount recurring (Netflix, Spotify)
        - "utility": Variable-amount monthly (electric, water, municipal)
        - "insurance": Periodic, mid-high amounts, often quarterly/annual
        - "debt_payment": Large fixed payments to financial institutions
        - "habitual": Frequent irregular spending (groceries, gas stations)
        - "uncategorized": Doesn't match any pattern
    """
    # Skip if not enough data
    if pattern.occurrence_count < 2:
        return ("uncategorized", 0.3)

    # Skip transfers - they're handled separately
    if pattern.is_transfer:
        return ("transfer", 0.9)

    merchant_lower = merchant_norm.lower()

    # --- KEYWORD-FIRST CHECKS ---
    # Some categories are best identified by keywords regardless of amount patterns
    # (e.g., AT&T is a utility even if it has fixed pricing)

    # Utility keywords - check these FIRST because some utilities have fixed pricing
    util_keywords = {'electric', 'power', 'energy', 'water', 'sewage', 'utility', 'utilities',
                    'city of', 'municipal', 'comcast', 'xfinity', 'at&t', 'att', 'verizon',
                    'spectrum', 'cox', 'internet', 'fiber', 'frontier', 'centurylink'}
    if any(kw in merchant_lower for kw in util_keywords):
        if pattern.cadence_label in ("monthly", "bimonthly") and pattern.occurrence_count >= 3:
            return ("utility", 0.95)

    # Debt payment keywords - mortgages, loans
    debt_keywords = {'mortgage', 'loan', 'lending', 'student loan', 'auto loan', 'car loan'}
    if any(kw in merchant_lower for kw in debt_keywords):
        if pattern.cadence_label == "monthly" and pattern.median_amount_cents >= 10000:
            return ("debt_payment", 0.95)

    # Insurance keywords
    ins_keywords = {'insurance', 'geico', 'state farm', 'allstate', 'progressive',
                   'liberty mutual', 'usaa', 'farmers', 'nationwide', 'aetna',
                   'cigna', 'united health', 'blue cross', 'humana'}
    if any(kw in merchant_lower for kw in ins_keywords):
        if pattern.is_recurring and pattern.median_amount_cents >= 5000:
            return ("insurance", 0.95)

    # Subscription keywords - streaming, software, gyms, etc.
    sub_keywords = {'netflix', 'spotify', 'hulu', 'disney', 'hbo', 'max', 'paramount',
                   'peacock', 'apple tv', 'prime video', 'crunchyroll', 'youtube',
                   'audible', 'kindle', 'apple music', 'amazon music', 'pandora', 'tidal',
                   'icloud', 'google one', 'dropbox', 'onedrive',
                   'adobe', 'microsoft 365', 'office 365', 'github', 'chatgpt', 'openai',
                   'notion', '1password', 'lastpass', 'dashlane',
                   'linkedin', 'coursera', 'udemy', 'skillshare', 'masterclass', 'duolingo',
                   'xbox', 'playstation', 'nintendo', 'steam', 'ea play',
                   'nytimes', 'washington post', 'wsj', 'medium', 'substack',
                   'peloton', 'planet fitness', 'la fitness', 'gold\'s gym', 'gym', 'fitness',
                   'strava', 'fitbit', 'patreon', 'twitch', 'discord', 'grammarly', 'canva',
                   'vpn', 'nordvpn', 'expressvpn'}
    if any(kw in merchant_lower for kw in sub_keywords):
        if pattern.is_recurring and pattern.median_amount_cents < 50000:  # Under $500
            return ("subscription", 0.95)

    # --- PATTERN-BASED CLASSIFICATION ---
    # For merchants without strong keyword signals, use pattern heuristics

    # --- DEBT PAYMENTS ---
    # Large fixed payments to financial institutions
    # Pattern: monthly, high amounts ($500+), low variance
    if (pattern.cadence_label == "monthly"
        and pattern.median_amount_cents >= 50000  # $500+
        and pattern.amount_cv < 0.10):  # Fairly consistent amounts
        # Check for financial institution keywords
        fin_keywords = {'bank', 'credit', 'card', 'chase', 'citi', 'wells', 'capital one'}
        if any(kw in merchant_lower for kw in fin_keywords):
            return ("debt_payment", 0.90)
        # Large recurring monthly = likely debt even without keywords
        if pattern.median_amount_cents >= 100000:  # $1000+
            return ("debt_payment", 0.75)

    # --- SUBSCRIPTIONS ---
    # Fixed-amount recurring charges
    # Pattern: regular cadence, very low variance (<5%), typically $5-$200
    if (pattern.is_recurring
        and not pattern.is_habitual
        and pattern.cadence_label in ("weekly", "monthly", "quarterly", "annual", "bimonthly")
        and pattern.amount_cv < 0.05  # Nearly identical amounts each time
        and pattern.median_amount_cents < 30000):  # Under $300 (utilities can be higher)

        # Higher confidence for streaming/software keywords
        sub_keywords = {'netflix', 'spotify', 'hulu', 'disney', 'hbo', 'youtube',
                       'apple', 'amazon prime', 'adobe', 'microsoft', 'dropbox',
                       'patreon', 'github', 'audible', 'kindle', 'gym', 'fitness',
                       'peloton', 'strava', 'medium', 'substack'}
        if any(kw in merchant_lower for kw in sub_keywords):
            return ("subscription", 0.95)
        return ("subscription", 0.80)

    # --- UTILITIES (pattern-based, no keywords) ---
    # Variable-amount monthly charges in typical utility range
    # Pattern: monthly, moderate variance (5-50%), $30-$1000 range
    if (pattern.cadence_label == "monthly"
        and pattern.occurrence_count >= 3
        and pattern.amount_cv >= 0.05  # Variable amounts
        and 3000 <= pattern.median_amount_cents <= 100000):  # $30-$1000 range
        # Monthly + variable amounts + reasonable range = likely a utility bill
        return ("utility", 0.75)

    # --- INSURANCE ---
    # Periodic payments, often higher amounts, can be monthly/quarterly/annual
    # Pattern: regular cadence, moderate-high amounts, moderate variance
    if (pattern.is_recurring
        and not pattern.is_habitual
        and pattern.cadence_label in ("monthly", "quarterly", "annual")
        and pattern.median_amount_cents >= 5000):  # $50+

        ins_keywords = {'insurance', 'geico', 'state farm', 'allstate', 'progressive',
                       'liberty mutual', 'usaa', 'farmers', 'nationwide', 'aetna',
                       'cigna', 'united health', 'blue cross', 'humana', 'premium'}
        if any(kw in merchant_lower for kw in ins_keywords):
            return ("insurance", 0.95)

        # High amounts with low variance could be insurance without keywords
        if pattern.amount_cv < 0.1 and pattern.median_amount_cents >= 20000:
            return ("insurance", 0.60)

    # --- HABITUAL SPENDING ---
    # Frequent but irregular (groceries, coffee, gas stations)
    if pattern.is_habitual:
        return ("habitual", 0.80)

    # --- FALLBACK ---
    return ("uncategorized", 0.30)


def classify_month(
    conn: sqlite3.Connection,
    year: int,
    month: int,
    patterns: dict[str, MerchantPattern] | None = None,
) -> list[ClassifiedTransaction]:
    """
    Classify all transactions in a given month.
    
    Returns list of ClassifiedTransaction with income/recurring/transfer/one-off labels.
    """
    if patterns is None:
        patterns = _detect_patterns(conn)
    
    # Get month boundaries
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    
    rows = conn.execute(
        """
        SELECT
            posted_at,
            amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS merchant_norm,
            COALESCE(merchant, '') AS merchant,
            COALESCE(description, '') AS description
        FROM transactions
        WHERE posted_at >= ? AND posted_at < ?
        ORDER BY posted_at
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    
    classified = []
    for r in rows:
        posted_at = datetime.fromisoformat(r["posted_at"]).date()
        amount_cents = r["amount_cents"]
        merchant_norm = r["merchant_norm"]
        raw_desc = r["merchant"] or r["description"] or ""
        
        pattern = patterns.get(merchant_norm)
        
        # Classification logic
        if amount_cents > 0:
            classification = "income"
        elif pattern and pattern.is_transfer:
            classification = "transfer"
        elif pattern and pattern.is_recurring:
            classification = "recurring"
        elif _is_transfer(merchant_norm):
            # Catch transfers not in patterns (e.g., first occurrence)
            classification = "transfer"
        else:
            classification = "one-off"
        
        classified.append(ClassifiedTransaction(
            posted_at=posted_at,
            amount_cents=amount_cents,
            merchant_norm=merchant_norm,
            raw_description=raw_desc,
            classification=classification,
            merchant_pattern=pattern,
        ))
    
    return classified


@dataclass
class MonthSummary:
    """Summary of a month's finances."""
    year: int
    month: int
    income_cents: int
    recurring_cents: int
    one_off_cents: int
    transfer_cents: int  # Excluded from analysis but tracked
    baseline_cents: int  # income - recurring
    net_cents: int       # income - recurring - one_off (transfers excluded)
    
    income_sources: list[tuple[str, int]]           # (merchant, total_cents)
    recurring_expenses: list[tuple[str, int, str]]  # (merchant, total_cents, cadence)
    one_off_expenses: list[tuple[str, int, int]]    # (merchant, total_cents, count)
    transfers: list[tuple[str, int]]                # (merchant, total_cents)
    
    @property
    def is_sustainable(self) -> bool:
        """True if recurring expenses are covered by income."""
        return self.baseline_cents >= 0
    
    @property
    def savings_cents(self) -> int:
        """Amount saved (or overspent if negative)."""
        return self.net_cents
    
    @property
    def buffer_cents(self) -> int:
        """Monthly buffer after recurring expenses."""
        return self.baseline_cents


def summarize_month(
    conn: sqlite3.Connection,
    year: int,
    month: int,
) -> MonthSummary:
    """
    Generate a complete financial summary for a month.
    """
    patterns = _detect_patterns(conn)
    classified = classify_month(conn, year, month, patterns)
    
    # Aggregate
    income_cents = 0
    recurring_cents = 0
    one_off_cents = 0
    transfer_cents = 0
    
    income_by_source: dict[str, int] = defaultdict(int)
    recurring_by_merchant: dict[str, tuple[int, str]] = {}  # merchant -> (cents, cadence)
    one_off_by_merchant: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))  # merchant -> (cents, count)
    transfer_by_merchant: dict[str, int] = defaultdict(int)
    
    for tx in classified:
        merchant = tx.merchant_norm or "(unknown)"
        
        if tx.classification == "income":
            income_cents += tx.amount_cents
            income_by_source[merchant] += tx.amount_cents
            
        elif tx.classification == "recurring":
            amt = abs(tx.amount_cents)
            recurring_cents += amt
            cadence = tx.merchant_pattern.cadence_label if tx.merchant_pattern else ""
            if merchant in recurring_by_merchant:
                existing_amt, existing_cadence = recurring_by_merchant[merchant]
                recurring_by_merchant[merchant] = (existing_amt + amt, existing_cadence)
            else:
                recurring_by_merchant[merchant] = (amt, cadence)
        
        elif tx.classification == "transfer":
            amt = abs(tx.amount_cents)
            transfer_cents += amt
            transfer_by_merchant[merchant] += amt
                
        else:  # one-off
            amt = abs(tx.amount_cents)
            one_off_cents += amt
            existing_amt, existing_count = one_off_by_merchant[merchant]
            one_off_by_merchant[merchant] = (existing_amt + amt, existing_count + 1)
    
    # Build sorted lists
    income_sources = sorted(income_by_source.items(), key=lambda x: x[1], reverse=True)
    recurring_expenses = sorted(
        [(m, c, cadence) for m, (c, cadence) in recurring_by_merchant.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    one_off_expenses = sorted(
        [(m, c, count) for m, (c, count) in one_off_by_merchant.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    transfers = sorted(transfer_by_merchant.items(), key=lambda x: x[1], reverse=True)
    
    return MonthSummary(
        year=year,
        month=month,
        income_cents=income_cents,
        recurring_cents=recurring_cents,
        one_off_cents=one_off_cents,
        transfer_cents=transfer_cents,
        baseline_cents=income_cents - recurring_cents,
        net_cents=income_cents - recurring_cents - one_off_cents,
        income_sources=income_sources,
        recurring_expenses=recurring_expenses,
        one_off_expenses=one_off_expenses,
        transfers=transfers,
    )


@dataclass 
class Alert:
    """A financial alert."""
    severity: str  # "high", "medium", "low"
    category: str  # "price_increase", "renewal", "new_merchant", "bundle", "income"
    title: str
    detail: str
    amount_cents: int | None = None


def detect_alerts(
    conn: sqlite3.Connection,
    year: int,
    month: int,
    patterns: dict[str, MerchantPattern] | None = None,
) -> list[Alert]:
    """
    Detect alerts for a given month:
    - Price increases (>10% or >$2 above median)
    - Upcoming annual renewals (within 30 days)
    - New merchants (first seen this month)
    - Bundle overlaps (multiple services in same family)
    """
    if patterns is None:
        patterns = _detect_patterns(conn)
    
    alerts: list[Alert] = []
    
    # Month boundaries
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    
    # Get this month's transactions (exclude transfers)
    rows = conn.execute(
        """
        SELECT
            posted_at,
            amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS merchant_norm
        FROM transactions
        WHERE posted_at >= ? AND posted_at < ?
          AND amount_cents < 0
        ORDER BY posted_at
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    
    this_month_merchants: dict[str, int] = {}  # merchant -> latest amount
    for r in rows:
        merchant = r["merchant_norm"]
        # Skip transfers for alert purposes
        if not _is_transfer(merchant):
            this_month_merchants[merchant] = abs(r["amount_cents"])
    
    # 1. Price increases (only for subscription-like recurring, not habitual)
    for merchant, current_cents in this_month_merchants.items():
        pattern = patterns.get(merchant)
        if pattern and pattern.is_recurring and not pattern.is_habitual and pattern.occurrence_count >= 3:
            median = pattern.median_amount_cents
            if median > 0:
                increase = current_cents - median
                pct = (increase / median) * 100
                # Flag if >10% or >$2 increase
                if increase > 200 or pct > 10:
                    alerts.append(Alert(
                        severity="medium",
                        category="price_increase",
                        title=f"Price increase: {merchant}",
                        detail=f"${median/100:.2f} → ${current_cents/100:.2f} (+{pct:.0f}%)",
                        amount_cents=increase,
                    ))
    
    # 2. Upcoming annual renewals
    today = date.today()
    for merchant, pattern in patterns.items():
        if pattern.is_recurring and pattern.cadence_label == "annual":
            # Estimate next charge
            if pattern.median_interval_days:
                next_expected = pattern.last_seen + timedelta(days=pattern.median_interval_days)
                days_until = (next_expected - today).days
                if 0 < days_until <= 30:
                    alerts.append(Alert(
                        severity="medium",
                        category="renewal",
                        title=f"Annual renewal soon: {merchant}",
                        detail=f"~${pattern.median_amount_cents/100:.2f} expected in {days_until} days",
                        amount_cents=pattern.median_amount_cents,
                    ))
    
    # 3. New merchants (first seen this month, not in patterns or first_seen is this month)
    for merchant in this_month_merchants:
        pattern = patterns.get(merchant)
        if pattern is None or (pattern.occurrence_count == 1 and pattern.first_seen >= start):
            amount = this_month_merchants[merchant]
            if amount >= 2000:  # Only flag if >= $20
                alerts.append(Alert(
                    severity="low",
                    category="new_merchant",
                    title=f"New merchant: {merchant}",
                    detail=f"${amount/100:.2f} - verify this is legitimate",
                    amount_cents=amount,
                ))
    
    # 4. Bundle overlaps
    bundle_families = {
        "disney_bundle": ["disney", "hulu", "espn"],
        "apple": ["apple", "icloud", "itunes", "app store"],
        "amazon": ["amazon prime", "prime video", "audible", "kindle"],
        "google": ["google", "youtube"],
        "microsoft": ["microsoft", "xbox", "office 365"],
    }
    
    family_matches: dict[str, list[str]] = defaultdict(list)
    for merchant in this_month_merchants:
        for family, keywords in bundle_families.items():
            if any(kw in merchant for kw in keywords):
                family_matches[family].append(merchant)
                break
    
    for family, merchants in family_matches.items():
        if len(merchants) >= 2:
            total = sum(this_month_merchants.get(m, 0) for m in merchants)
            alerts.append(Alert(
                severity="medium",
                category="bundle",
                title=f"Possible duplicate: {family.replace('_', ' ').title()}",
                detail=f"{len(merchants)} services: {', '.join(merchants[:3])}",
                amount_cents=total,
            ))
    
    # Sort by severity
    severity_order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: (severity_order.get(a.severity, 99), -(a.amount_cents or 0)))

    return alerts


# ---------------------------------------------------------------------------
# Sketchy Charge Detection
# ---------------------------------------------------------------------------

@dataclass
class SketchyCharge:
    """A suspicious/sketchy charge."""
    posted_at: date
    merchant_norm: str
    amount_cents: int
    pattern_type: str       # "duplicate_charge", "unusual_amount", "test_charge", etc.
    severity: str           # "high", "medium", "low"
    detail: str             # Human-readable explanation
    related_txn_date: date | None = None  # For duplicates, the other charge date
    # Transaction details for drill-down
    account_name: str | None = None
    raw_description: str | None = None
    # Related transactions (for patterns like duplicates)
    related_transactions: list[tuple[date, int, str]] | None = None  # (date, amount_cents, description)


def _normalize_merchant_fuzzy(merchant: str) -> str:
    """
    Normalize merchant name for fuzzy matching.
    Strips common suffixes like .COM, INC, LLC, etc.
    Also handles payment processor prefixes like AMAZON*PRIME -> amazon.
    """
    import re
    s = merchant.lower().strip()
    # Handle payment processor prefixes (AMAZON*PRIME -> amazon)
    if '*' in s:
        s = s.split('*')[0]
    # Remove common business suffixes
    s = re.sub(r'\s*(\.com|\.net|\.org|inc\.?|llc\.?|ltd\.?|corp\.?|\*+)$', '', s)
    # Remove trailing numbers (often transaction IDs)
    s = re.sub(r'\s+\d{4,}$', '', s)
    # Remove extra whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def detect_sketchy(
    conn: sqlite3.Connection,
    days: int = 60,
    account_filter: list[str] | None = None,
) -> list[SketchyCharge]:
    """
    Detect sketchy/suspicious charges.

    Patterns detected:
    - Duplicate charge: Same merchant + amount within 3 days
    - Unusual amount: >2x median for that merchant
    - Test charge: $0.01-$1.00 amounts
    - Round amount spike: $50/$100/$200 exact, first time from merchant
    - Rapid-fire charges: 3+ charges from same merchant in 24h
    - Refund + recharge: Refund followed by similar charge

    Respects learned rules from user feedback to suppress false positives.
    """
    from . import db as dbmod

    # Get suppressed patterns from learned rules
    suppressed = dbmod.get_suppressed_patterns(conn)
    trusted_merchants = dbmod.get_trusted_merchants(conn)

    since = (date.today() - timedelta(days=days)).isoformat()

    # Build query with optional account filter
    sql = """
        SELECT
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm,
            COALESCE(t.merchant, t.description, '') AS raw_description,
            a.name AS account_name
        FROM transactions t
        LEFT JOIN accounts a ON t.account_id = a.account_id
        WHERE t.posted_at >= ?
    """
    params: list = [since]

    if account_filter is not None:
        if not account_filter:  # Empty list = no accounts
            return []
        placeholders = ",".join("?" * len(account_filter))
        sql += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    sql += " ORDER BY merchant_norm, t.posted_at"
    rows = conn.execute(sql, params).fetchall()

    sketchy: list[SketchyCharge] = []

    # Group by merchant with full details
    by_merchant: dict[str, list[tuple[date, int, str, str]]] = defaultdict(list)
    for r in rows:
        d = datetime.fromisoformat(r["posted_at"]).date()
        by_merchant[r["merchant_norm"]].append((d, r["amount_cents"], r["raw_description"], r["account_name"]))

    # Get historical patterns for unusual amount detection
    patterns = _detect_patterns(conn, lookback_days=400)

    # Track all charges for duplicate detection (with extra details)
    all_charges: list[tuple[date, str, int, str, str]] = []
    for r in rows:
        d = datetime.fromisoformat(r["posted_at"]).date()
        all_charges.append((d, r["merchant_norm"], r["amount_cents"], r["raw_description"], r["account_name"]))

    seen_duplicates: set[tuple[str, int, str, str]] = set()  # (merchant, amount, date1, date2)

    # 1. Duplicate charges: Same merchant + amount + account within 3 days
    # Note: Cross-account duplicates are handled separately by detect_cross_account_duplicates
    for i, (d1, m1, a1, desc1, acct1) in enumerate(all_charges):
        if a1 >= 0:  # Only expenses
            continue
        for j, (d2, m2, a2, desc2, acct2) in enumerate(all_charges):
            if j <= i:
                continue
            if m1 != m2 or a1 != a2 or acct1 != acct2:
                continue  # Must be same merchant, amount, AND account
            delta = abs((d2 - d1).days)
            if delta <= 3 and delta > 0:
                key = (m1, a1, d1.isoformat(), d2.isoformat())
                if key not in seen_duplicates:
                    seen_duplicates.add(key)
                    sketchy.append(SketchyCharge(
                        posted_at=d2,
                        merchant_norm=m1,
                        amount_cents=abs(a1),
                        pattern_type="duplicate_charge",
                        severity="high",
                        detail=f"Same charge ${abs(a1)/100:.2f} on {d1} and {d2}",
                        related_txn_date=d1,
                        account_name=acct2,
                        raw_description=desc2,
                        related_transactions=[(d1, abs(a1), desc1), (d2, abs(a2), desc2)],
                    ))

    # 2. Unusual amount (>2x median for that merchant)
    # Requires 3+ HISTORICAL charges (not counting the recent unusual one)
    for merchant, items in by_merchant.items():
        pattern = patterns.get(merchant)
        # Need 4+ total occurrences so there's 3+ historical when excluding current
        if pattern and pattern.occurrence_count >= 4:
            median = pattern.median_amount_cents
            if median > 0:
                for d, amount, desc, acct in items:
                    if amount < 0:  # Expense
                        amt = abs(amount)
                        if amt > median * 2 and amt > 2000:  # >2x and >$20
                            sketchy.append(SketchyCharge(
                                posted_at=d,
                                merchant_norm=merchant,
                                amount_cents=amt,
                                pattern_type="unusual_amount",
                                severity="high",
                                detail=f"${amt/100:.2f} is {amt/median:.1f}x the usual ${median/100:.2f}",
                                account_name=acct,
                                raw_description=desc,
                            ))

    # 3. Test charges ($0.01-$1.00)
    for d, merchant, amount, desc, acct in all_charges:
        if amount < 0:
            amt = abs(amount)
            if 1 <= amt <= 100:  # $0.01 - $1.00
                sketchy.append(SketchyCharge(
                    posted_at=d,
                    merchant_norm=merchant,
                    amount_cents=amt,
                    pattern_type="test_charge",
                    severity="medium",
                    detail=f"Possible test/verification charge: ${amt/100:.2f}",
                    account_name=acct,
                    raw_description=desc,
                ))

    # 4. Round amount spike: $50/$100/$200 exact, first time from merchant
    # Check against PATTERNS (400-day lookback), not just detection window
    round_amounts = {5000, 10000, 15000, 20000, 25000}  # In cents
    for merchant, items in by_merchant.items():
        pattern = patterns.get(merchant)
        # Only flag if this is truly a new merchant (no pattern history)
        if pattern is None or pattern.occurrence_count == 1:
            items_sorted = sorted(items, key=lambda x: x[0])
            if len(items_sorted) >= 1:
                d, amount, desc, acct = items_sorted[0]
                if amount < 0 and abs(amount) in round_amounts:
                    sketchy.append(SketchyCharge(
                        posted_at=d,
                        merchant_norm=merchant,
                        amount_cents=abs(amount),
                        pattern_type="round_amount_spike",
                        severity="medium",
                        detail=f"First charge from merchant is a round ${abs(amount)/100:.0f}",
                        account_name=acct,
                        raw_description=desc,
                    ))

    # 5. Rapid-fire charges: 3+ from same merchant in 24h
    for merchant, items in by_merchant.items():
        items_sorted = sorted(items, key=lambda x: x[0])
        expenses = [(d, a, desc, acct) for d, a, desc, acct in items_sorted if a < 0]

        # Check for bursts
        for i, (d1, a1, desc1, acct1) in enumerate(expenses):
            count_in_day = 1
            total_cents = abs(a1)
            related = [(d1, abs(a1), desc1)]
            for j in range(i + 1, len(expenses)):
                d2, a2, desc2, acct2 = expenses[j]
                if (d2 - d1).days <= 1:
                    count_in_day += 1
                    total_cents += abs(a2)
                    related.append((d2, abs(a2), desc2))
                else:
                    break
            if count_in_day >= 3:
                # Only report once per burst
                sketchy.append(SketchyCharge(
                    posted_at=d1,
                    merchant_norm=merchant,
                    amount_cents=total_cents,
                    pattern_type="rapid_fire",
                    severity="medium",
                    detail=f"{count_in_day} charges within 24h totaling ${total_cents/100:.2f}",
                    account_name=acct1,
                    raw_description=desc1,
                    related_transactions=related,
                ))
                break  # One alert per merchant

    # 6. Refund + recharge pattern
    for merchant, items in by_merchant.items():
        items_sorted = sorted(items, key=lambda x: x[0])
        for i, (d1, a1, desc1, acct1) in enumerate(items_sorted):
            if a1 <= 0:  # Looking for refunds (positive amounts as credits)
                continue
            # Check for similar charge within 7 days after refund
            for j in range(i + 1, len(items_sorted)):
                d2, a2, desc2, acct2 = items_sorted[j]
                if a2 >= 0:
                    continue
                delta_days = (d2 - d1).days
                if delta_days > 7:
                    break
                # Check if amounts are similar (within 20%)
                if abs(abs(a2) - a1) <= a1 * 0.2:
                    sketchy.append(SketchyCharge(
                        posted_at=d2,
                        merchant_norm=merchant,
                        amount_cents=abs(a2),
                        pattern_type="refund_recharge",
                        severity="low",
                        detail=f"Refund ${a1/100:.2f} on {d1}, then charge ${abs(a2)/100:.2f} on {d2}",
                        related_txn_date=d1,
                        account_name=acct2,
                        raw_description=desc2,
                        related_transactions=[(d1, a1, f"Refund: {desc1}"), (d2, abs(a2), desc2)],
                    ))
                    break

    # Filter out suppressed patterns based on learned rules
    filtered_sketchy = []
    for alert in sketchy:
        merchant = alert.merchant_norm
        pattern_type = alert.pattern_type

        # Skip if merchant is fully trusted
        if merchant in trusted_merchants:
            continue

        # Skip if this pattern type is suppressed for this merchant
        if merchant in suppressed and pattern_type in suppressed[merchant]:
            continue

        filtered_sketchy.append(alert)

    # Sort by severity and date
    severity_order = {"high": 0, "medium": 1, "low": 2}
    filtered_sketchy.sort(key=lambda x: (severity_order.get(x.severity, 99), -x.amount_cents))

    return filtered_sketchy


# ---------------------------------------------------------------------------
# Duplicate Subscription Detection
# ---------------------------------------------------------------------------

@dataclass
class DuplicateGroup:
    """A group of potentially duplicate subscriptions."""
    group_type: str          # "same_merchant", "fuzzy_match", "similar_pattern"
    merchants: list[str]     # Merchant names in this group
    total_monthly_cents: int # Combined monthly cost
    severity: str            # "high", "medium", "low"
    detail: str              # Human-readable explanation
    items: list[tuple[str, int, str]]  # (merchant, monthly_cents, cadence)


def detect_duplicates(
    conn: sqlite3.Connection,
    days: int = 400,
    account_filter: list[str] | None = None,
) -> list[DuplicateGroup]:
    """
    Detect duplicate or overlapping subscriptions.

    Types detected:
    - Same merchant charged multiple times in billing cycle
    - Fuzzy merchant matching: "NETFLIX" vs "NETFLIX.COM" vs "NETFLIX INC"
    - Similar subscriptions: Same amount +/- 10%, same cadence, different names
    - Known bundle families (Disney, Apple, Amazon, Google, Microsoft)
    """
    from . import db as dbmod

    duplicates: list[DuplicateGroup] = []
    patterns = _detect_patterns(conn, lookback_days=days, account_filter=account_filter)

    # Get dismissed duplicates
    dismissed_duplicates = dbmod.get_dismissed_duplicates(conn)

    # Get recurring patterns only
    recurring = {
        k: v for k, v in patterns.items()
        if v.is_recurring and not v.is_transfer and not v.is_habitual
    }

    # 1. Fuzzy merchant matching
    fuzzy_groups: dict[str, list[str]] = defaultdict(list)
    for merchant in recurring:
        normalized = _normalize_merchant_fuzzy(merchant)
        if normalized:
            fuzzy_groups[normalized].append(merchant)

    for normalized, merchants in fuzzy_groups.items():
        if len(merchants) >= 2:
            items = []
            total_monthly = 0
            for m in merchants:
                p = recurring[m]
                # Estimate monthly cost
                if p.cadence_label == "annual":
                    monthly = p.median_amount_cents // 12
                elif p.cadence_label == "quarterly":
                    monthly = p.median_amount_cents // 3
                elif p.cadence_label == "bimonthly":
                    monthly = p.median_amount_cents // 2
                elif p.cadence_label == "weekly":
                    monthly = round(p.median_amount_cents * 52 / 12)  # ~4.33x
                elif p.cadence_label == "biweekly":
                    monthly = round(p.median_amount_cents * 26 / 12)  # ~2.17x
                else:
                    monthly = p.median_amount_cents

                items.append((m, monthly, p.cadence_label))
                total_monthly += monthly

            duplicates.append(DuplicateGroup(
                group_type="fuzzy_match",
                merchants=merchants,
                total_monthly_cents=total_monthly,
                severity="high",
                detail=f"Similar merchant names: {', '.join(merchants[:3])}",
                items=items,
            ))

    # 2. Similar subscriptions - DISABLED
    # This was causing false positives by grouping unrelated merchants
    # with similar prices (e.g., Netflix and Spotify both at $15.99).
    # Keep only fuzzy matching and bundle family detection.
    # TODO: Re-enable with better heuristics (e.g., require partial name overlap)

    # 3. Known bundle families
    bundle_families = {
        "Disney Bundle": ["disney", "hulu", "espn"],
        "Apple": ["apple", "icloud", "itunes", "app store", "apple tv", "apple music"],
        "Amazon": ["amazon prime", "prime video", "audible", "kindle", "amazon music"],
        "Google": ["google", "youtube", "google one", "google play"],
        "Microsoft": ["microsoft", "xbox", "office 365", "microsoft 365", "game pass"],
        "Streaming": ["netflix", "hbo", "max", "paramount", "peacock", "showtime"],
    }

    for family_name, keywords in bundle_families.items():
        matches = []
        for merchant, pattern in recurring.items():
            if any(kw in merchant for kw in keywords):
                matches.append((merchant, pattern))

        if len(matches) >= 2:
            items = []
            total_monthly = 0
            for m, p in matches:
                if p.cadence_label == "annual":
                    monthly = p.median_amount_cents // 12
                elif p.cadence_label == "quarterly":
                    monthly = p.median_amount_cents // 3
                else:
                    monthly = p.median_amount_cents
                items.append((m, monthly, p.cadence_label))
                total_monthly += monthly

            duplicates.append(DuplicateGroup(
                group_type="bundle_family",
                merchants=[m for m, _ in matches],
                total_monthly_cents=total_monthly,
                severity="medium",
                detail=f"{family_name}: {len(matches)} overlapping services",
                items=items,
            ))

    # Sort by total cost
    duplicates.sort(key=lambda x: -x.total_monthly_cents)

    # Remove duplicate groups (a merchant shouldn't appear in multiple similar groups)
    # Also filter out groups where any merchant is dismissed
    seen_merchants: set[str] = set()
    deduped: list[DuplicateGroup] = []
    for group in duplicates:
        # Skip if any merchant in this group has been dismissed
        if any(m in dismissed_duplicates for m in group.merchants):
            continue
        # Check if any merchant is already in a group
        if not any(m in seen_merchants for m in group.merchants):
            deduped.append(group)
            seen_merchants.update(group.merchants)

    return deduped


@dataclass
class CrossAccountDuplicate:
    """A potential duplicate transaction across different accounts."""
    date1: date
    date2: date
    amount_cents: int
    merchant1: str
    merchant2: str
    account1: str
    account2: str
    account1_name: str
    account2_name: str
    similarity_score: float  # 0-1, how similar the transactions are


def detect_cross_account_duplicates(
    conn: sqlite3.Connection,
    days: int = 60,
    account_filter: list[str] | None = None,
) -> list[CrossAccountDuplicate]:
    """
    Detect potential duplicate transactions across different accounts.

    This catches cases where the same transaction might be recorded in
    multiple accounts, which could indicate:
    - A transfer being double-counted
    - Data import issues
    - Actual duplicates that need review

    Criteria:
    - Same amount (exact match)
    - Same date or within 2 days
    - Different accounts
    - Similar merchant names (fuzzy match)
    """
    since = (date.today() - timedelta(days=days)).isoformat()

    # Get account names
    account_names = {}
    for row in conn.execute("SELECT account_id, name FROM accounts").fetchall():
        account_names[row["account_id"]] = row["name"]

    # Query all transactions
    query = """
        SELECT
            t.account_id,
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm
        FROM transactions t
        WHERE t.posted_at >= ?
    """
    params: list = [since]

    if account_filter and len(account_filter) > 1:
        # Only makes sense with multiple accounts
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    query += " ORDER BY t.amount_cents, t.posted_at"
    rows = conn.execute(query, params).fetchall()

    # Group by amount for efficient comparison
    by_amount: dict[int, list] = defaultdict(list)
    for row in rows:
        by_amount[row["amount_cents"]].append(row)

    duplicates: list[CrossAccountDuplicate] = []
    seen_pairs: set[tuple] = set()  # Avoid duplicate reports

    for amount, txns in by_amount.items():
        if len(txns) < 2:
            continue

        # Compare all pairs with same amount
        for i, t1 in enumerate(txns):
            for t2 in txns[i + 1:]:
                # Must be different accounts
                if t1["account_id"] == t2["account_id"]:
                    continue

                # Parse dates
                d1 = date.fromisoformat(t1["posted_at"][:10])
                d2 = date.fromisoformat(t2["posted_at"][:10])

                # Must be within 2 days
                if abs((d2 - d1).days) > 2:
                    continue

                # Check merchant similarity
                m1 = t1["merchant_norm"]
                m2 = t2["merchant_norm"]
                similarity = _merchant_similarity(m1, m2)

                # Need some similarity (but not necessarily exact match)
                if similarity < 0.3:
                    continue

                # Avoid duplicate pair reports
                pair_key = tuple(sorted([
                    (t1["account_id"], t1["posted_at"], amount),
                    (t2["account_id"], t2["posted_at"], amount)
                ]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                duplicates.append(CrossAccountDuplicate(
                    date1=d1,
                    date2=d2,
                    amount_cents=amount,
                    merchant1=m1,
                    merchant2=m2,
                    account1=t1["account_id"],
                    account2=t2["account_id"],
                    account1_name=account_names.get(t1["account_id"], "Unknown"),
                    account2_name=account_names.get(t2["account_id"], "Unknown"),
                    similarity_score=similarity,
                ))

    # Sort by date descending
    duplicates.sort(key=lambda x: x.date1, reverse=True)
    return duplicates


def _merchant_similarity(m1: str, m2: str) -> float:
    """
    Calculate similarity between two merchant names.
    Returns 0-1 score.
    """
    if not m1 or not m2:
        return 0.0

    # Exact match
    if m1 == m2:
        return 1.0

    # One contains the other
    if m1 in m2 or m2 in m1:
        return 0.9

    # Word overlap
    words1 = set(m1.split())
    words2 = set(m2.split())
    if words1 and words2:
        intersection = words1 & words2
        union = words1 | words2
        jaccard = len(intersection) / len(union)
        if jaccard > 0:
            return 0.5 + (jaccard * 0.4)  # Scale to 0.5-0.9

    # First word match (often the merchant name)
    w1 = m1.split()[0] if m1 else ""
    w2 = m2.split()[0] if m2 else ""
    if len(w1) > 3 and len(w2) > 3 and (w1.startswith(w2[:4]) or w2.startswith(w1[:4])):
        return 0.5

    return 0.0


def get_subscriptions(
    conn: sqlite3.Connection,
    days: int = 400,
    account_filter: list[str] | None = None,
) -> list[tuple[str, int, str, date, date, bool, str, bool, str | None, int]]:
    """
    Get all detected subscriptions with duplicate flags and transaction type.

    Uses PATTERN-BASED classification to identify subscriptions:
    - Fixed-amount recurring charges (low coefficient of variation)
    - Regular cadence (weekly, monthly, annual)
    - Keywords boost confidence but aren't required

    Also includes KNOWN subscription services (Netflix, Spotify, etc.) even if
    they only have 1-2 charges, flagging them as "known" services.

    Manual overrides take precedence.

    Returns: list of (merchant, monthly_cents, cadence, first_seen, last_seen,
                      is_duplicate, txn_type, is_known_service, display_name,
                      actual_charge_cents)
    """
    from . import db as dbmod

    patterns = _detect_patterns(conn, lookback_days=days, account_filter=account_filter)
    duplicates = detect_duplicates(conn, days, account_filter=account_filter)

    # Get manual type overrides
    type_overrides = dbmod.get_recurring_type_overrides(conn)

    # Build set of duplicate merchants
    duplicate_merchants: set[str] = set()
    for group in duplicates:
        duplicate_merchants.update(group.merchants)

    # Get transaction history for known service detection (second pass)
    since = (date.today() - timedelta(days=days)).isoformat()
    hist_query = """
        SELECT
            TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS merchant_norm,
            amount_cents,
            posted_at
        FROM transactions
        WHERE posted_at >= ?
    """
    hist_params: list = [since]
    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        hist_query += f" AND account_id IN ({placeholders})"
        hist_params.extend(account_filter)
    hist_query += " ORDER BY merchant_norm, posted_at"
    history_query = conn.execute(hist_query, hist_params).fetchall()

    # Group history by merchant (for second pass - known services with few charges)
    merchant_history: dict[str, dict] = {}
    for row in history_query:
        m = row["merchant_norm"]
        if m not in merchant_history:
            merchant_history[m] = {"amounts": [], "dates": [], "days": []}
        merchant_history[m]["amounts"].append(row["amount_cents"])
        merchant_history[m]["dates"].append(row["posted_at"])

    # Track which merchants we've processed to avoid duplicates
    processed_merchants: set[str] = set()
    subscriptions = []

    # First pass: Process merchants with detected patterns (3+ occurrences)
    for merchant, pattern in patterns.items():
        # Skip transfers always
        if pattern.is_transfer:
            continue

        # Skip non-recurring (but known services can still be added in second pass)
        if not pattern.is_recurring:
            continue

        # Check for manual override first
        override = type_overrides.get(merchant.lower())
        if override == "bill":
            # User manually set this as a bill, skip from subscriptions
            processed_merchants.add(merchant)
            continue
        if override == "ignore":
            # User dismissed this from recurring lists entirely
            processed_merchants.add(merchant)
            continue

        # Use PATTERN-BASED classification (primary method)
        txn_type, confidence = classify_by_pattern(pattern, merchant)

        # Check if this is a known subscription service
        known_match = _match_known_subscription(merchant)
        is_known = known_match is not None
        display_name = known_match[0] if known_match else None

        # If user manually set this as subscription, include it
        if override == "subscription":
            txn_type = "subscription"
        # Known services are always treated as subscriptions
        elif is_known:
            txn_type = "subscription"
        # Only include subscriptions (pattern-detected)
        # Utilities go to bills, not here
        elif txn_type != "subscription":
            continue

        # Calculate monthly cost
        if pattern.cadence_label == "annual":
            monthly = pattern.median_amount_cents // 12
        elif pattern.cadence_label == "quarterly":
            monthly = pattern.median_amount_cents // 3
        elif pattern.cadence_label == "bimonthly":
            monthly = pattern.median_amount_cents // 2
        elif pattern.cadence_label == "weekly":
            monthly = round(pattern.median_amount_cents * 52 / 12)  # ~4.33x
        elif pattern.cadence_label == "biweekly":
            monthly = round(pattern.median_amount_cents * 26 / 12)  # ~2.17x
        else:
            monthly = pattern.median_amount_cents

        is_dup = merchant in duplicate_merchants
        processed_merchants.add(merchant)

        subscriptions.append((
            merchant,
            monthly,
            pattern.cadence_label,
            pattern.first_seen,
            pattern.last_seen,
            is_dup,
            txn_type,
            is_known,
            display_name,
            pattern.median_amount_cents,  # actual charge amount
        ))

    # Second pass: Check for KNOWN subscription services with 1-2 charges
    # These wouldn't be detected by the pattern detection (needs 3+ occurrences)
    for merchant, hist_data in merchant_history.items():
        if merchant in processed_merchants:
            continue

        # Check if this matches a known subscription service
        known_match = _match_known_subscription(merchant)
        if not known_match:
            continue

        display_name, typical_cadence = known_match

        # Skip transfers
        if _is_transfer(merchant):
            continue

        # Check for manual override
        override = type_overrides.get(merchant.lower())
        if override == "bill":
            continue
        if override == "ignore":
            continue

        # Get amounts and dates
        amounts = hist_data.get("amounts", [])
        dates = hist_data.get("dates", [])
        if not amounts or not dates:
            continue

        # Only include expenses (negative amounts)
        expense_amounts = [abs(a) for a in amounts if a < 0]
        expense_dates = [d for a, d in zip(amounts, dates) if a < 0]
        if not expense_amounts:
            continue

        # Calculate stats
        median_amount = int(statistics.median(expense_amounts))
        first_seen = datetime.fromisoformat(min(expense_dates)).date()
        last_seen = datetime.fromisoformat(max(expense_dates)).date()

        # Infer cadence from actual transaction pattern
        # If only 1-2 charges and it's been 45+ days since last charge with no new one,
        # and amount is $40+, it's likely annual (not the default from KNOWN_SUBSCRIPTIONS)
        days_since_last = (date.today() - last_seen).days
        inferred_cadence = typical_cadence

        if len(expense_amounts) <= 2:
            if days_since_last >= 45 and median_amount >= 4000:  # $40+
                # Likely annual - no charge in 45+ days for a decent amount
                inferred_cadence = "annual"
            elif len(expense_amounts) == 2:
                # Check interval between the two charges
                dates_sorted = sorted(expense_dates)
                interval = (datetime.fromisoformat(dates_sorted[1]).date() -
                           datetime.fromisoformat(dates_sorted[0]).date()).days
                if interval >= 300:  # ~10+ months between charges
                    inferred_cadence = "annual"
                elif interval >= 80:  # ~3 months
                    inferred_cadence = "quarterly"

        # Calculate monthly cost based on inferred cadence
        if inferred_cadence == "annual":
            monthly = median_amount // 12
            cadence_label = "annual"
        elif inferred_cadence == "quarterly":
            monthly = median_amount // 3
            cadence_label = "quarterly"
        else:
            monthly = median_amount
            cadence_label = "monthly"

        is_dup = merchant in duplicate_merchants

        subscriptions.append((
            merchant,
            monthly,
            cadence_label,
            first_seen,
            last_seen,
            is_dup,
            "subscription",  # txn_type
            True,            # is_known_service
            display_name,    # display_name
            median_amount,   # actual charge amount
        ))

    # Sort by monthly cost descending
    subscriptions.sort(key=lambda x: -x[1])

    return subscriptions


def get_bills(
    conn: sqlite3.Connection,
    days: int = 400,
    account_filter: list[str] | None = None,
) -> list[tuple[str, int, str, date, date, str]]:
    """
    Get utility bills (electric, gas, internet, phone).

    Uses PATTERN-BASED classification (not keyword matching) to identify utilities:
    - Monthly cadence with variable amounts
    - Amount range typical for utilities ($30-$1000)
    - Keywords boost confidence but aren't required

    Manual overrides take precedence.

    Returns: list of (merchant, monthly_cents, cadence, first_seen, last_seen, txn_type)
    """
    from . import db as dbmod

    patterns = _detect_patterns(conn, lookback_days=days, account_filter=account_filter)

    # Get manual type overrides
    type_overrides = dbmod.get_recurring_type_overrides(conn)

    bills = []
    for merchant, pattern in patterns.items():
        # Skip transfers
        if pattern.is_transfer:
            continue

        # Skip non-recurring
        if not pattern.is_recurring:
            continue

        # Check for manual override first
        override = type_overrides.get(merchant.lower())
        if override == "subscription":
            # User manually set this as a subscription, skip from bills
            continue
        if override == "ignore":
            # User dismissed this from recurring lists entirely
            continue

        # Use PATTERN-BASED classification (primary method)
        txn_type, confidence = classify_by_pattern(pattern, merchant)

        # If user manually set this as bill, include it regardless of pattern
        if override == "bill":
            txn_type = "utility"
        # Include utilities and insurance (both are "bills" in the broader sense)
        elif txn_type not in ("utility", "insurance"):
            continue

        # Calculate monthly cost
        if pattern.cadence_label == "annual":
            monthly = pattern.median_amount_cents // 12
        elif pattern.cadence_label == "quarterly":
            monthly = pattern.median_amount_cents // 3
        elif pattern.cadence_label == "bimonthly":
            monthly = pattern.median_amount_cents // 2
        elif pattern.cadence_label == "weekly":
            monthly = round(pattern.median_amount_cents * 52 / 12)  # ~4.33x
        elif pattern.cadence_label == "biweekly":
            monthly = round(pattern.median_amount_cents * 26 / 12)  # ~2.17x
        else:
            monthly = pattern.median_amount_cents

        bills.append((
            merchant,
            monthly,
            pattern.cadence_label,
            pattern.first_seen,
            pattern.last_seen,
            txn_type,
        ))

    # Sort by monthly cost descending
    bills.sort(key=lambda x: -x[1])

    return bills


def detect_price_changes(
    conn: sqlite3.Connection,
    days: int = 180,
    min_change_pct: float = 10.0,
    max_change_pct: float = 100.0,
    account_filter: list[str] | None = None,
) -> list[dict]:
    """
    Detect subscription price changes.

    Looks at recurring charges and identifies when the amount changed significantly.
    Filters out extreme changes (>100%) which are usually detection errors.

    Args:
        conn: Database connection
        days: Days of history to analyze
        min_change_pct: Minimum percentage change to report (default 10%)
        max_change_pct: Maximum percentage change to report (default 100%)
        account_filter: Optional list of account IDs to filter

    Returns:
        List of dicts with: merchant, old_amount, new_amount, change_pct, change_date, display_name
    """
    since = (date.today() - timedelta(days=days)).isoformat()

    # Get transaction history grouped by merchant
    query = """
        SELECT
            TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS merchant_norm,
            amount_cents,
            posted_at
        FROM transactions
        WHERE posted_at >= ? AND amount_cents < 0
    """
    params: list = [since]
    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND account_id IN ({placeholders})"
        params.extend(account_filter)
    query += " ORDER BY merchant_norm, posted_at"

    rows = conn.execute(query, params).fetchall()

    # Group by merchant
    merchant_history: dict[str, list[tuple[int, str]]] = {}
    for merchant, amount, posted_at in rows:
        if not merchant:
            continue
        if merchant not in merchant_history:
            merchant_history[merchant] = []
        merchant_history[merchant].append((abs(amount), posted_at))

    price_changes = []

    for merchant, history in merchant_history.items():
        if len(history) < 3:
            continue  # Need at least 3 charges to detect a change

        # Sort by date
        history.sort(key=lambda x: x[1])

        # Look for amount changes
        # Compare most recent charge to the one before
        amounts = [h[0] for h in history]
        dates = [h[1] for h in history]

        # Get the most common "old" amount (excluding last 2 charges)
        if len(amounts) >= 4:
            old_amounts = amounts[:-2]
            old_amount = max(set(old_amounts), key=old_amounts.count)  # Mode

            # Check if the old amounts were consistent (subscription-like)
            # Skip if old amounts have high variance (habitual spending)
            if len(old_amounts) >= 2:
                mean_old = sum(old_amounts) / len(old_amounts)
                if mean_old > 0:
                    # Calculate how many amounts match the mode vs total
                    mode_count = old_amounts.count(old_amount)
                    consistency_ratio = mode_count / len(old_amounts)
                    # Require at least 60% of charges to be the same amount
                    if consistency_ratio < 0.6:
                        continue  # Too variable, likely not a subscription

            # Get the most recent amount
            new_amount = amounts[-1]

            # Check if there's a significant change
            if old_amount > 0 and new_amount != old_amount:
                change_pct = ((new_amount - old_amount) / old_amount) * 100

                if min_change_pct <= abs(change_pct) <= max_change_pct:
                    # Find when the change happened
                    change_date = None
                    for i, amt in enumerate(amounts):
                        if amt == new_amount and (i == 0 or amounts[i-1] == old_amount):
                            change_date = dates[i]
                            break

                    # Look up display name from known subscriptions
                    known = _match_known_subscription(merchant)
                    display_name = known[0] if known else None

                    price_changes.append({
                        "merchant": merchant,
                        "display_name": display_name,
                        "old_amount": old_amount,
                        "new_amount": new_amount,
                        "change_pct": change_pct,
                        "change_date": change_date,
                    })

    # Sort by change percentage descending (biggest increases first)
    price_changes.sort(key=lambda x: -x["change_pct"])

    return price_changes
