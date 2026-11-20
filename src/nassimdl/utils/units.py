"""Human-readable formatting for sizes, speeds, and durations."""


def format_size(size_bytes: int | float) -> str:
    """Format bytes into human-readable size (e.g., '12.4 MB')."""
    if size_bytes < 0:
        return "Unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units[:-1]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} {units[-1]}"


def format_speed(bytes_per_second: float) -> str:
    """Format download speed (e.g., '12.4 MB/s')."""
    if bytes_per_second <= 0:
        return "0 B/s"
    return f"{format_size(bytes_per_second)}/s"


def format_eta(seconds: float | None) -> str:
    """Format ETA in human-readable form (e.g., '2:15:30' or '∞')."""
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "∞"
    seconds = int(seconds)
    if seconds == 0:
        return "0s"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    elif minutes > 0:
        return f"{minutes}:{secs:02d}"
    else:
        return f"{secs}s"


def parse_size(size_str: str) -> int:
    """Parse human-readable size string to bytes (e.g., '50MB' -> 52428800)."""
    size_str = size_str.strip().upper()
    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
    }
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if size_str.endswith(suffix):
            num_str = size_str[: -len(suffix)].strip()
            return int(float(num_str) * mult)
    return int(float(size_str))
