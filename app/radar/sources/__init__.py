"""Source adapters for the Content Opportunity Radar."""

from app.radar.sources.base import SourceAdapter, default_transport
from app.radar.sources.reddit import RedditSource
from app.radar.sources.rss import RSSSource
from app.radar.sources.gsc import GSCAdapter, build_gsc_adapter, READONLY_SCOPE

__all__ = [
    "SourceAdapter",
    "default_transport",
    "RedditSource",
    "RSSSource",
    "GSCAdapter",
    "build_gsc_adapter",
    "READONLY_SCOPE",
]
